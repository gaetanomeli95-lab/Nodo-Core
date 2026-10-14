"""Voice runtime (Phase 2): drives a VoiceSession through the Core over a typed event channel.

The runtime is transport-agnostic: `emit` sends outbound protocol dicts (WebSocket today); `handle()` accepts
parsed inbound messages (or raw audio bytes). Every spoken/textual input enters the SAME `NodoCore.handle()`
pipeline as the text channel — voice is a transport, not a separate assistant.

Key semantics:
- turns execute only on FINAL transcripts (or end-of-speech for server-side STT);
- interrupt ("fermati"/"basta"/"stop" or an `interrupt` message) is a real event: it supersedes the turn
  generation, suppresses every further outbound event of that turn, drains TTS and lands in LISTENING;
- approvals are bound: a spoken "sì" resolves ONLY the pending approval of this session;
- every turn records latency stamps (TURN_STAMPS) persisted to `voice_turn_metrics`.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text as text_sql
from sqlalchemy.exc import OperationalError

from nodo.app import Container, ensure_workspace
from nodo.core.events import EventBus
from nodo.db.models import Approval, Conversation, Message, VoiceTurnMetric
from nodo.voice import live as live_engine
from nodo.voice import protocol
from nodo.voice.base import (
    SpeechEvent,
    VoiceSession,
    VoiceState,
)

log = logging.getLogger("nodo.voice")

SessionFactory = Callable[[], Any]  # context manager yielding a SQLAlchemy Session


class InterruptRequested(Exception):
    """Internal control flow: the current turn generation was superseded by an interrupt."""


async def _retry_locked(fn: Callable[[], Any], attempts: int = 14) -> None:
    """Run a short synchronous database write; if SQLite says 'locked', yield to the event loop and retry."""
    for n in range(attempts):
        try:
            fn()
            return
        except OperationalError as e:
            if "locked" not in str(e).lower() or n == attempts - 1:
                raise
            await asyncio.sleep(min(2.0, 0.15 * 2 ** n))


_VOCATIVE = re.compile(r"^\s*(nodo|node|hey nodo|ehi nodo)[,.\s]+", re.IGNORECASE)
_STOP_RE = re.compile(
    r"^\s*((no[, ]+)?(stop|fermati|ferma|basta|annulla|cancel|chiudi|smetti|silenzio|sta zitto|zitto)"
    r"[.\!\s]*)$", re.IGNORECASE)
_STOP_WORD = re.compile(r"\b(ferma|fermati|basta|stop|zitta|zitto|aspetta|silenzio)\b", re.IGNORECASE)
_APPROVE_RE = re.compile(
    r"^\s*(sì|si|yes|ok|okay|va bene|conferma|confermo|procedi|vai pure|approvo|d'accordo)[.\!\s]*$",
    re.IGNORECASE)
_REJECT_RE = re.compile(
    r"^\s*(no|nope|non procedere|non confermo|rifiuto|lascia stare|non farlo)[.\!\s]*$", re.IGNORECASE)

_MIN_UTTERANCE_BYTES = 16000 * 2 * 0.35   # < 350 ms of 16 kHz PCM is a click, not speech
_MAX_UTTERANCE_BYTES = 16000 * 2 * 60     # keep the last minute at most

# event types from the core stream that are forwarded verbatim (renamed agent.*/tool.* already match protocol)
_FORWARD = {"agent.started", "agent.finished", "tool.started", "tool.finished", "tool.denied", "model.failed",
            "ui.present"}


THINK_DECL = {
    "name": "pensa",
    "description": ("Il cervello di Maria (memoria completa, registro, ricordi, ricerche, file, azioni). Usalo per "
                    "QUALSIASI cosa che non sia un saluto o una battuta. Restituisce la risposta da dire."),
    "parameters": {"type": "object", "properties": {
        "richiesta": {"type": "string", "description": "Le parole di Gaetano, così come le ha dette."}},
        "required": ["richiesta"]},
}
BOARD_EVERY_S = 60
_VOICE_TAGS = re.compile(r"<\s*no[ _]speech\s*>|<[a-z_ ]{1,24}>|\{[a-z_ ]{1,24}\}", re.I)
_LIVE_OWN_TOOLS = {"decide", "close_screen"}   # yes/no on the proposal shown, close what is on screen


class VoiceRuntime:
    SPEECH_TAIL_S = 90.0   # max wait for the voice to finish the last sentences of a turn

    def __init__(self, session: VoiceSession, container: Container,
                 emit: Callable[[dict], Any], session_factory: SessionFactory):
        self.vs = session
        self.c = container
        self._emit = emit          # async callable(dict): sends a protocol event to the client
        self._db: SessionFactory = session_factory
        self._gen = 0              # turn generation: interrupt/new turn invalidates the previous pipeline
        self._seq = 0
        self._dead = False
        self._turn_task: asyncio.Task | None = None
        self._speech_q: asyncio.Queue[str | None] = asyncio.Queue()
        self._audio = bytearray()
        self._audio_mime = "audio/webm"
        self._chunk_mime: str | None = None   # mime of the frames actually received (pcm from the worklet)
        self._request_id: str | None = None
        self._stt_iter: AsyncIterator[SpeechEvent] | None = None
        self._stt_task: asyncio.Task | None = None
        # --- Gemini Live engine (voice_engine=auto + key configured): real speech-to-speech ---
        self._live: live_engine.LiveBridge | None = None
        self._live_wanted = False         # live engine available: it connects on the FIRST input, not at page load
        self._live_used = False           # the session has carried real conversation: worth reconnecting
        self._live_reconnects = 0         # transparent reconnects attempted (never loop forever)
        self._live_failed_models: set[str] = set()   # models that dropped us: skipped on reconnect
        self._live_muted = False          # "Ferma" pressed: drop outgoing audio until the turn ends
        self._in_text = ""                # accumulated input transcription of the current turn
        self._out_text: list[str] = []    # accumulated output transcription (what NODO is saying)
        self._live_connect_lock = asyncio.Lock()
        self._live_recovery: asyncio.Task | None = None
        self._live_pending_drop: str | None = None
        self._live_rotation: asyncio.Task | None = None
        self._live_tools: dict[str, asyncio.Task] = {}
        self._live_tool_lock = asyncio.Lock()
        self._think_ids: set[str] = set()         # the «pensa» call in flight (only the newest one runs)
        self._think_keys: dict[str, tuple] = {}
        self._think_results: dict[tuple, dict] = {}
        self._superseded: set[str] = set()
        self._stopped_gens: set[int] = set()
        self._board_task: asyncio.Task | None = None
        self._board_version = ""                  # the lavagna viva the live voice has in front of it
        self._board_due = False    # turns WE stopped («Ferma»): Gemini does not know, it must be told
        self._live_results: dict[str, dict] = {}
        self._pensa_error: str | None = None   # a failed «pensa» is not retried within the same turn
        self._babble_checked = False             # one look per turn at what was heard
        self._babble = False
        self._barge_from: int | None = None    # where Maria started talking in the transcript: «ferma» after it stops her
        self._live_user_emitted = False

    # ------------------------------------------------------------- inbound dispatch
    async def handle(self, msg) -> None:
        from nodo.voice.protocol import (
            ApprovalAnswer,
            AudioChunk,
            ConversationSet,
            Interrupt,
            SessionEnd,
            SessionStart,
            SpeechEnd,
            TranscriptFinal,
            TranscriptPartial,
        )
        if isinstance(msg, (bytes, bytearray)):
            msg = AudioChunk(data=protocol.encode_audio(bytes(msg)))
        try:
            if isinstance(msg, SessionStart):
                await self._on_start(msg)
            elif isinstance(msg, ConversationSet):
                await self._set_conversation(msg.conversation_id)
            elif isinstance(msg, SessionEnd):
                await self.close()
            elif isinstance(msg, AudioChunk):
                await self._on_audio(msg)
            elif isinstance(msg, SpeechEnd):
                await self._on_speech_end()
            elif isinstance(msg, TranscriptPartial):
                await self._on_partial(msg.text)
            elif isinstance(msg, TranscriptFinal):
                await self._on_final(msg.text)
            elif isinstance(msg, Interrupt):
                await self.interrupt(reason=msg.reason)
            elif isinstance(msg, ApprovalAnswer):
                await self._answer_approval(msg.approval_id, msg.decision)
            elif isinstance(msg, protocol.Greet):
                await self._greet(msg.text)
        except InterruptRequested:
            pass
        except Exception as e:  # the session must survive a single bad message
            log.exception("voice message handling failed")
            await self._send("error", where="handler", message=f"{type(e).__name__}: {e}")

    async def _on_start(self, msg) -> None:
        vs = self.vs
        vs.conversation_id = msg.conversation_id or vs.conversation_id
        vs.language = msg.language or vs.language
        self._audio_mime = msg.audio_mime
        if vs.state == VoiceState.CONNECTING:
            await self._set_state(VoiceState.IDLE)
        self._live_failed_models = set()
        self._live_reconnects = 0
        self._live_used = False
        self._live_wanted = self._want_live()
        # the live session opens lazily on the first real input: no idle sessions dying every 20s
        engine = "live" if self._live_wanted else self._fallback_engine()
        self._log("voice.session.started", {"language": vs.language, "engine": engine})
        quota_active = engine != "live" and live_engine.quota_note(self.c.settings) is not None
        await self._send("voice.session.started", state=str(vs.state), conversation_id=vs.conversation_id,
                         stt=self.c.stt.name, tts=self.c.tts.name, engine=engine,
                         reason="quota" if quota_active else None)

    async def _set_conversation(self, conversation_id: str | None) -> None:
        """Sync text and voice to the same thread; never switch during an active turn or approval."""
        vs = self.vs
        if ((self._live_recovery and not self._live_recovery.done()) or
                any(not task.done() for task in self._live_tools.values())):
            await self._send("error", where="conversation", message="Attendi che NODO finisca il lavoro in corso")
            return
        if vs.turn_active or vs.pending_approval_id or vs.state in (
            VoiceState.ACTING, VoiceState.SPEAKING, VoiceState.WAITING_FOR_APPROVAL
        ):
            await self._send("error", where="conversation",
                             message="Termina il turno o l'approvazione prima di cambiare conversazione")
            return
        if conversation_id:
            with self._db() as sdb:
                org, _ = ensure_workspace(sdb)
                conv = sdb.get(Conversation, conversation_id)
                if conv is None or conv.organization_id != org.id:
                    await self._send("error", where="conversation", message="Conversazione non trovata")
                    return
        if vs.conversation_id != conversation_id and self._live is not None:
            # The Live model has the previous thread in its prompt. Recreate it on the
            # next input so it cannot answer with stale context from another project.
            old, self._live = self._live, None
            await old.close()
            self._live_used = False
            self._live_failed_models.clear()
            self._live_reconnects = 0
            self._live_muted = False
        vs.conversation_id = conversation_id
        await self._send("voice.conversation", conversation_id=conversation_id)

    def _want_live(self) -> bool:
        return live_engine.missing_reason(self.c.settings, self.c) is None

    def _fallback_engine(self) -> str:
        """`api` = the server transcribes the microphone PCM and speaks through a cloud TTS (Gemini standard
        API): the same mic/player path as Live, without barge-in by voice. `browser` = Web Speech API."""
        return "api" if getattr(self.c.stt, "accepts_pcm", False) and self.c.tts.location != "browser" else "browser"

    async def _ensure_live(self) -> bool:
        """Opens the live session on demand. False = fell back to the browser chain for good."""
        if self._live is not None:
            return True
        if not self._live_wanted:
            return False
        if self._live_recovery and not self._live_recovery.done():
            return False  # don't replay stale microphone audio into a new turn
        try:
            async with self._live_connect_lock:
                if self._live is None:
                    await asyncio.wait_for(self._start_live(), timeout=self.c.settings.live_connect_timeout)
            return True
        except Exception as e:
            log.warning("live voice engine failed to start: %s", live_engine.safe_error(e))
            self._live = None
            await self._fallback_live(live_engine.safe_error(e))
            return False

    async def _fallback_live(self, reason: str) -> None:
        quota = live_engine._is_quota_error(reason)
        if quota:
            live_engine._mark_quota(self.c.settings)
        self._live_wanted = False
        self._audio.clear()
        self.vs.turn_active = False
        engine = self._fallback_engine()
        where = "la voce Gemini standard (qualche secondo di attesa in più)" if engine == "api" else "la voce del browser"
        message = (f"Gemini Live ha raggiunto un limite di quota. Continuo con {where}; "
                   "controlla le quote del progetto in Google AI Studio." if quota else
                   f"La voce dal vivo si è fermata ({live_engine.safe_error(reason)}). "
                   f"Clicca il microfono per continuare con {where}.")
        await self._send("error", where="live", message=message)
        await self._send("voice.engine", engine=engine, reason="quota" if quota else "connection")
        if self.vs.state not in (VoiceState.CLOSED, VoiceState.WAITING_FOR_APPROVAL):
            await self._set_state(VoiceState.IDLE)

    async def _on_audio(self, msg: protocol.AudioChunk) -> None:
        vs = self.vs
        if self._live_wanted:
            if not await self._ensure_live() or self._live is None:
                return                                   # the mic PCM cannot feed the browser chain anyway
            if vs.state == VoiceState.IDLE:
                await self._set_state(VoiceState.LISTENING)
            self._live_used = True
            vs.mark("speech_started")
            try:
                await self._live.send_audio(protocol.decode_audio(msg.data))  # Gemini hears you directly
            except live_engine.LiveDropped as e:
                await self._live_dropped(str(e))
            return
        stt = self.c.stt
        # Late Live frames cannot be decoded by the browser WebM STT chain; a PCM-capable STT buffers them.
        if (msg.mime or "").startswith("audio/pcm") and not getattr(stt, "accepts_pcm", False):
            return
        if vs.state in (VoiceState.IDLE, VoiceState.WAITING_FOR_APPROVAL):
            # waiting for a confirmation: keep listening; a «sì»/«no» is bound to vs.pending_approval_id only
            await self._set_state(VoiceState.LISTENING)
        if vs.state != VoiceState.LISTENING:
            return  # audio outside a listening phase is ignored (no silent capture)
        vs.mark("speech_started")
        chunk = protocol.decode_audio(msg.data)
        if msg.mime:
            self._chunk_mime = msg.mime
        self._audio += chunk
        if len(self._audio) > _MAX_UTTERANCE_BYTES:        # ~60 s of 16 kHz PCM: never grow without bound
            del self._audio[:len(self._audio) - _MAX_UTTERANCE_BYTES]
        if hasattr(stt, "begin"):  # streaming STT provider
            if self._stt_task is None or self._stt_task.done():
                self._stt_iter = stt.begin(vs.language)
                self._stt_task = asyncio.create_task(self._pump_stt())
            await stt.feed(chunk, msg.mime or self._audio_mime)

    async def _on_speech_end(self) -> None:
        vs = self.vs
        vs.mark("speech_end")
        if self._live is not None:
            try:
                await self._live.end_audio()
            except live_engine.LiveDropped as e:
                await self._live_dropped(str(e))
            return
        stt = self.c.stt
        if self._stt_task is not None and hasattr(stt, "end"):
            await self._set_state(VoiceState.TRANSCRIBING)
            await stt.end()  # the pump delivers final -> turn
            return
        if self._audio:
            await self._set_state(VoiceState.TRANSCRIBING)
            audio, self._audio = bytes(self._audio), bytearray()
            mime = self._chunk_mime or self._audio_mime
            if mime.startswith("audio/pcm") and len(audio) < _MIN_UTTERANCE_BYTES:
                await self._set_state(VoiceState.LISTENING)       # a click or a cough: nothing to transcribe
                return
            try:
                t = await stt.transcribe(audio, mime, vs.language)
            except Exception as e:
                await self._send("error", where="stt", message=f"{type(e).__name__}: {e}")
                await self._set_state(VoiceState.LISTENING)
                return
            if not t.text.strip():
                await self._set_state(VoiceState.LISTENING)       # silence or noise: keep listening quietly
                return
            await self._send("transcript.final", text=t.text, provider=t.provider)
            await self._begin_turn(t.text)
        elif vs.state == VoiceState.LISTENING:
            await self._set_state(VoiceState.IDLE)

    async def _pump_stt(self) -> None:
        gen = self._gen
        assert self._stt_iter is not None
        try:
            async for ev in self._stt_iter:
                if self._gen != gen or self._dead:
                    return
                if ev.kind == "partial":
                    self.vs.mark("transcript_partial_first")
                    await self._send("transcript.partial", text=ev.text)
                elif ev.kind == "final":
                    await self._send("transcript.final", text=ev.text, provider=self.c.stt.name)
                    await self._begin_turn(ev.text)
                elif ev.kind == "error":
                    await self._send("error", where="stt", message=ev.error or "stt error")
                    await self._set_state(VoiceState.LISTENING)
        except Exception as e:
            if self._gen == gen:
                await self._send("error", where="stt", message=f"{type(e).__name__}: {e}")

    async def _on_partial(self, text: str) -> None:
        if self._live is not None:
            return   # in live mode transcripts come from Gemini's own input transcription
        vs = self.vs
        if vs.state == VoiceState.IDLE:
            await self._set_state(VoiceState.LISTENING)
        if vs.state == VoiceState.LISTENING:
            await self._set_state(VoiceState.TRANSCRIBING)
        vs.mark("speech_started")
        vs.mark("transcript_partial_first")
        await self._send("transcript.partial", text=text)

    async def _on_final(self, text: str) -> None:
        self.vs.mark("speech_end")
        if self._live_wanted:           # typed text also works in a live session: Gemini answers it
            if await self._ensure_live() and self._live is not None:
                if not self.vs.turn_active:
                    self.vs.begin_turn()
                    self.vs.turn_active = True
                self._in_text = text
                self._live_user_emitted = True
                self._live_used = True
                await self._send("transcript.final", text=text, provider="client")
                try:
                    await self._live.inject(text, respond=True)
                except live_engine.LiveDropped as e:
                    await self._live_dropped(str(e))
                return
        await self._send("transcript.final", text=text, provider="client")
        await self._begin_turn(text)

    # ------------------------------------------------------------- turns
    async def _begin_turn(self, text: str) -> None:
        from nodo.memory.mind import is_noise
        if is_noise(text):                     # other alphabets, «egg», a cough: not a question, stay quiet
            self._log("voice.noise_ignored", {"text": text[:60]})
            await self._set_state(VoiceState.LISTENING)
            return
        if self._turn_task and not self._turn_task.done() and not self.vs.interrupted:
            await self._send("error", where="turn", message="turn already in progress")
            return
        self._turn_task = asyncio.create_task(self.run_turn(text))

    async def run_turn(self, text: str) -> None:
        """One user turn through the Core. Suppression rule: after an interrupt (`_gen` changes) the rest of
        the turn may complete internally (records stay consistent) but emits nothing and no audio."""
        vs = self.vs
        vs.begin_turn()
        vs.turn_active = True
        self._request_id = None
        gen = self._gen
        interrupted = False
        # one speech queue PER TURN: a stop marker left by an interrupted turn can never mute the next one
        q: asyncio.Queue[str | None] = asyncio.Queue()
        self._speech_q = q
        speaker = asyncio.create_task(self._speak_loop(gen, q))
        try:
            await self._pipeline(text, gen)
        except InterruptRequested:
            interrupted = True
        except Exception as e:
            if self._gen == gen:
                log.exception("voice turn failed")
                with suppress(InterruptRequested):
                    await self._send("error", where="turn", message=f"{type(e).__name__}: {e}")
                    await self._set_state(VoiceState.ERROR)
        finally:
            q.put_nowait(None)
            # let the voice FINISH the answer (a local Piper sentence can take several seconds): the loop stops
            # by itself on an interruption; the long cap only guards against a TTS that hangs for ever
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(speaker, timeout=self.SPEECH_TAIL_S)
            if "tts_started" in vs.stamps:
                vs.mark("tts_done")
            vs.mark("turn_done")
            self._persist_metrics(interrupted)
            vs.turn_active = False
            if self._gen == gen:  # not superseded
                if vs.pending_approval_id and vs.state != VoiceState.WAITING_FOR_APPROVAL:
                    await self._set_state(VoiceState.WAITING_FOR_APPROVAL)   # the proposal is still open
                elif vs.state == VoiceState.INTERRUPTED:
                    await self._set_state(VoiceState.LISTENING)
                elif vs.state not in (VoiceState.IDLE, VoiceState.LISTENING, VoiceState.CLOSED,
                                      VoiceState.WAITING_FOR_APPROVAL):
                    await self._set_state(VoiceState.IDLE)
                if not interrupted:
                    with suppress(InterruptRequested):
                        await self._send("voice.turn.done", state=str(vs.state), latency_ms=vs.latency_ms())

    async def _pipeline(self, text: str, gen: int) -> None:
        vs = self.vs
        text = _VOCATIVE.sub("", text.strip())
        # --- fast paths (2H/2J): no reasoning pipeline ---
        if _STOP_RE.match(text):
            self._log("voice.stop_command", {"text": text[:120]})
            await self.interrupt(reason="stop_command")
            raise InterruptRequested
        if vs.pending_approval_id and (_APPROVE_RE.match(text) or _REJECT_RE.match(text)):
            await self._resolve_pending(approve=bool(_APPROVE_RE.match(text)), source="voice")
            return
        if _APPROVE_RE.match(text) or _REJECT_RE.match(text):
            # a bare yes/no with nothing pending is never an authorization
            await self._send("approval.ignored", reason="no pending approval")
        # --- normal path: same Core as text ---
        vs.mark("transcript_final")
        await self._set_state(VoiceState.UNDERSTANDING)
        with self._db() as sdb:
            core = self.c.core(sdb)
            async for e in core.handle(text, vs.conversation_id, channel="voice", language=vs.language):
                if self._gen != gen or self._dead:
                    raise InterruptRequested
                t = e["type"]
                self._request_id = self._request_id or e.get("request_id")
                if t == "intent":
                    vs.mark("intent_resolved")
                    await self._send("nodo.intent", **{k: e[k] for k in
                                     ("name", "entity", "from_context", "reference_reapplied", "confidence")
                                     if k in e})
                elif t == "plan":
                    await self._set_state(VoiceState.THINKING)
                    await self._send("nodo.plan", steps=e["steps"])
                elif t == "context":
                    vs.mark("context_built")
                    await self._send("nodo.context", sources=e["sources"], size=e["size"],
                                     sensitivity=e["sensitivity"])
                elif t == "step":
                    if e["step"].startswith(("agent:", "tool:")) and e["status"] == "running":
                        await self._set_state(VoiceState.ACTING)
                    await self._send("nodo.step", step=e["step"], status=e["status"])
                elif t == "approval.requested":
                    vs.pending_approval_id = e["approval_id"]
                    await self._set_state(VoiceState.WAITING_FOR_APPROVAL)
                    await self._send("approval.required", approval_id=e["approval_id"], tool=e["tool"],
                                     action=e["action"], level=e["level"])
                elif t == "token":
                    vs.mark("model_first_token")
                    if vs.state in (VoiceState.THINKING, VoiceState.ACTING):
                        await self._set_state(VoiceState.SPEAKING)
                    await self._send("nodo.token", delta=e["delta"])
                elif t == "sentence":
                    self._speech_q.put_nowait(e["text"])
                    await self._send("nodo.sentence", text=e["text"])
                elif t == "done":
                    vs.mark("model_done")
                    vs.conversation_id = e["conversation_id"]
                    await self._send("nodo.response", text=e["text"], conversation_id=e["conversation_id"],
                                     intent=e["intent"], provider=e["provider"], model=e["model"],
                                     confidence=e["confidence"], agents=e["agents"])
                elif t == "error":
                    await self._send("error", where="core", message=e["message"])
                elif t in _FORWARD:
                    await self._send(t, **{k: v for k, v in e.items() if k not in ("type", "request_id", "at")})

    # ------------------------------------------------------------- Gemini Live engine
    async def _start_live(self, resume_model: str | None = None, resume_handle: str | None = None) -> None:
        """Opens the real-time session: persona + tools identical to the text brain, but audio in/out."""
        from nodo.brain.board import board_for
        from nodo.brain.loop import Brain
        from nodo.brain.persona import build_live_persona
        from nodo.brain.tools import Toolbox, recent_history

        rid = f"voice-live-{self.vs.id[:8]}"
        with self._db() as sdb:
            core = self.c.core(sdb)
            conv = core._conversation(self.vs.conversation_id, "voice")
            self.vs.conversation_id = conv.id
            # the shop, already read: the voice answers register questions from here in a second
            board = board_for(core.org)
            board.refresh(sdb, core.org, self.c.settings, self._now())
            self._board_version = board.version
            system = build_live_persona(self.c.settings, Brain(core, conv, rid, "voice").snapshot()
                                        + ("\n\n" + board.text if board.text else ""),
                                        delegated=self.c.settings.live_brain)
            hist = recent_history(sdb, conv.id, 8)
            if hist and not resume_handle:
                system += ("\n\nConversazione recente (contesto, non rispondere a queste righe):\n"
                           + "\n".join(f"{m['role']}: {m['content'][:300]}" for m in hist))
            decls = [{"name": s.name, "description": s.description, "parameters": s.parameters}
                     for s in Toolbox(core, conv, rid, "", "voice").specs()]
            if self.c.settings.live_brain:
                # Gemini is fast but shallow and claims things it did not do: it keeps only the ears, the mouth and
                # the yes/no of a decision; every real answer comes from the Claude brain through «pensa»
                decls = [THINK_DECL] + [d for d in decls if d["name"] in _LIVE_OWN_TOOLS]
            # the text brain writes «[silenzio]» to stay quiet; a VOICE reads it aloud («silenzio, silenzio…»)
            system = system.replace("scrivi soltanto [silenzio]", "stai zitta, non dire nessuna parola")
            # Keep the wait explicit across Live models: the voice must use the actual tool result,
            # not improvise an answer while the brain is still working.
            decls = [{**d, "behavior": "BLOCKING"} for d in decls]
            sdb.commit()  # Advertise only a durable, valid conversation ID.
        bridge = live_engine.LiveBridge(self.c.settings, self._on_live_event,
                                       getattr(self.c, "live_connect", None))
        try:
            await bridge.start(system, decls, self.vs.language, exclude=self._live_failed_models,
                               resume_model=resume_model, resume_handle=resume_handle)
            self._live = bridge
            if self._board_task is None or self._board_task.done():
                self._board_task = asyncio.create_task(self._board_loop())
            await self._send("voice.conversation", conversation_id=self.vs.conversation_id)
            await self._send("voice.connection", status="connected", model=bridge.model,
                             resumed=bool(bridge.resume_handle))
        except BaseException:
            await bridge.close()  # also close on cancellation/timeout
            raise

    def _now(self) -> datetime:
        from nodo.night.briefing import tzinfo
        return datetime.now(tzinfo(self.c.settings.timezone))

    async def _board_loop(self) -> None:
        """The car that never stops: the shop is re-read every minute; the voice gets the new page only when
        something changed, and never in the middle of an answer."""
        while not self._dead:
            await asyncio.sleep(BOARD_EVERY_S)
            with suppress(Exception):
                await self._board_push()

    async def _board_push(self) -> None:
        if self._live is None or self._dead:
            return
        if self.vs.turn_active or self.vs.state not in (VoiceState.LISTENING, VoiceState.IDLE):
            self._board_due = True                 # after this answer
            return
        self._board_due = False
        from nodo.brain.board import board_for

        def read() -> tuple[str, str, str]:
            with self._db() as sdb:
                org = self.c.core(sdb).org
                b = board_for(org)
                b.refresh(sdb, org, self.c.settings, self._now())
                return b.version, b.text, f"{b.at:%H:%M}" if b.at else ""
        version, text, at = await asyncio.to_thread(read)
        if not text or version == self._board_version or self._live is None:
            return
        await self._live.inject(f"[Lavagna aggiornata alle {at}: dati nuovi, non rispondere a questo messaggio]\n"
                                + text, respond=False)
        self._board_version = version

    async def _live_go(self, *path: VoiceState) -> None:
        """Walk the state machine step by step: skips transitions that are not allowed."""
        for to in path:
            await self._set_state(to)

    async def _on_live_event(self, ev: dict) -> None:
        """Normalized Live event -> protocol events for the panel (+ tool execution on this side)."""
        try:
            await self._on_live_event_inner(ev)
        except InterruptRequested:
            pass            # the panel is gone or the turn was superseded: nothing more to send

    async def _on_live_event_inner(self, ev: dict) -> None:
        vs, kind = self.vs, ev["kind"]
        if self._dead:
            return
        if kind in ("audio", "output") and not self._live_muted and self._babble_gate():
            return
        if kind == "audio":
            if self._live_muted:
                return
            if not vs.turn_active:
                vs.begin_turn()
                vs.turn_active = True
            vs.mark("tts_started")
            vs.mark("tts_first_audio")
            if self._barge_from is None:
                self._barge_from = len(self._in_text)
            if vs.state != VoiceState.SPEAKING:
                await self._live_go(VoiceState.UNDERSTANDING, VoiceState.THINKING, VoiceState.SPEAKING)
            await self._send("tts.chunk", audio=protocol.encode_audio(ev["data"]),
                             mime=live_engine.OUT_MIME)
        elif kind == "input":                                # Gemini transcribing what you are saying
            if not vs.turn_active:
                vs.begin_turn()
                vs.turn_active = True
                vs.mark("speech_started")
                self._in_text = ""
                self._babble = self._babble_checked = False
            self._in_text += ev["text"]
            vs.mark("transcript_partial_first")
            await self._send("transcript.partial", text=self._in_text)
            if (self._barge_from is not None and not self._live_muted
                    and _STOP_WORD.search(self._in_text[self._barge_from:])):
                # «ferma, ferma»: stop at once, do not wait for Gemini to notice it was interrupted
                await self.interrupt(reason="stop_word")
                return
            if vs.state != VoiceState.UNDERSTANDING:
                await self._live_go(VoiceState.LISTENING, VoiceState.UNDERSTANDING)
        elif kind == "output":                               # Gemini transcribing its own answer
            await self._live_finalize_input()
            if self._live_muted:
                return
            self._out_text.append(ev["text"])
            said = "".join(self._out_text).strip().lower().lstrip("[(«\"' ")
            if said and len(said) <= 12 and "silenzio".startswith(said[:8]) and len(said) >= 4:
                self._live_muted = True     # it started reading «[silenzio]» aloud: stop relaying at once
                self._out_text.clear()
                await self._send("voice.interrupted", reason="quiet", during="SPEAKING")
                return
            vs.mark("model_first_token")
            if vs.state != VoiceState.SPEAKING:
                await self._live_go(VoiceState.UNDERSTANDING, VoiceState.THINKING, VoiceState.SPEAKING)
            await self._send("nodo.token", delta=ev["text"])
        elif kind == "tool_call":                            # same toolbox and guards as the text brain
            await self._live_finalize_input()
            ev = {**ev, "user_text": self._in_text, "generation": self._gen}
            if vs.state != VoiceState.ACTING:
                await self._live_go(VoiceState.IDLE, VoiceState.UNDERSTANDING,
                                    VoiceState.THINKING, VoiceState.ACTING)
            # Keep receiving interruptions, resumption tokens and GoAway during slow tools.
            self._live_tools = {key: task for key, task in self._live_tools.items() if not task.done()}
            cid = ev["id"]
            if ev.get("name") == THINK_DECL["name"]:
                key = self._think_key(ev)
                ev["request_key"] = key
                self._think_keys[cid] = key
                for old_id in [k for k in self._think_ids if k != cid]:
                    if (self._think_keys.get(old_id) != key and
                            (task := self._live_tools.get(old_id)) and not task.done()):
                        self._superseded.add(old_id)
                        task.cancel()
                self._think_ids.add(cid)
            if cid not in self._live_tools or self._live_tools[cid].done():
                self._live_tools[cid] = asyncio.create_task(self._run_live_tool(ev, self._live))
        elif kind == "interrupted":                          # you spoke over it: Gemini stopped itself
            self._in_text = ""
            self._live_user_emitted = False
            self._out_text.clear()
            self._live_muted = False
            self._pensa_error = None
            if self.vs.state != VoiceState.CLOSED:
                try:
                    vs.interrupt()
                except Exception:
                    pass
                self._gen += 1
                self._cancel_live_thoughts()
                await self._send("voice.interrupted", reason="barge_in", during="SPEAKING")
                await self._set_state(VoiceState.LISTENING)
        elif kind == "turn_complete":
            await self._live_end_turn()
        elif kind == "go_away":
            # Give an active reply time to finish, but renew before the advertised deadline.
            if self._live_rotation and not self._live_rotation.done():
                return
            raw = str(ev.get("time_left", "5s"))
            try:
                seconds = float(raw.rstrip("s"))
            except ValueError:
                seconds = 5
            async def rotate():
                await asyncio.sleep(max(0, min(seconds - 1, 30)) if vs.turn_active else 0)
                await self._live_dropped("rotazione programmata")
            self._live_rotation = asyncio.create_task(rotate())
        elif kind == "tool_cancelled":
            for cid in ev["ids"]:
                if task := self._live_tools.get(cid):
                    task.cancel()
        elif kind == "closed":
            await self._live_dropped(str(ev.get("error") or ""))

    async def _run_live_tool(self, call: dict, bridge) -> None:
        try:
            async with self._live_tool_lock:
                if self._live is bridge and not self._dead:
                    if call.get("generation", self._gen) == self._gen:
                        await self._live_tool_calls([call], bridge)
                    elif call.get("generation") in self._stopped_gens:
                        await self._close_call(call, bridge, "Gaetano ti ha fermata: non dire niente e ascoltalo.")
        except asyncio.CancelledError:
            if call.get("id") in self._superseded:
                # replaced by a newer question: close the old call so Gemini does not wait on it forever
                self._superseded.discard(call["id"])
                await self._close_call(call, bridge, "Superata da una domanda più nuova: non dire niente.")
                return
            if call.get("generation") in self._stopped_gens:
                # «Ferma» from the panel or the stop word: Gemini never saw an interruption and, with BLOCKING
                # tools, would wait for this result forever (Maria deaf until the session restarts)
                await self._close_call(call, bridge, "Gaetano ti ha fermata: non dire niente e ascoltalo.")
                return
            raise
        except InterruptRequested:
            log.debug("live tool finished after the panel went away")   # the browser closed: nothing to tell
        except live_engine.LiveDropped as e:
            if self._live is bridge:
                await self._live_dropped(str(e))
        except Exception:
            log.exception("live tool task failed")
        finally:
            self._think_ids.discard(call.get("id"))
            self._think_keys.pop(call.get("id"), None)

    async def _close_call(self, call: dict, bridge, note: str) -> None:
        if self._live is bridge and bridge is not None and not self._dead and call.get("id"):
            with suppress(Exception):
                await bridge.tool_response([(call["id"], call["name"], {"risposta": "", "nota": note})])

    def _think_key(self, call: dict) -> tuple:
        def key(text):
            return " ".join(str(text or "").casefold().split()).rstrip(".?!")
        heard = call.get("user_text", self._in_text)
        return (self._gen, self.vs.turn_number,
                key(call.get("args", {}).get("richiesta") or heard), key(heard))

    def _cancel_live_thoughts(self) -> None:
        for cid in tuple(self._think_ids):
            if (task := self._live_tools.get(cid)) and not task.done():
                task.cancel()

    async def _live_dropped(self, reason: str) -> None:
        """Single recovery owner; receive callbacks must not reconnect inside their own pump."""
        if self._live is None or self._dead:
            return
        if self._live_recovery and not self._live_recovery.done():
            self._live_pending_drop = reason
            return
        async def recover():
            try:
                await self._recover_live(reason)
            finally:
                self._live_recovery = None
                pending, self._live_pending_drop = self._live_pending_drop, None
                if pending is not None and not self._dead and self._live is not None:
                    await self._live_dropped(pending)
        self._live_recovery = asyncio.create_task(recover())

    async def _recover_live(self, reason: str) -> None:
        if self._live is None:
            return
        live, self._live = self._live, None
        kind = live_engine.error_kind(reason)
        model, handle = live.model, live.resume_handle
        if kind == "configuration" and model:
            self._live_failed_models.add(model)
            handle = None
        await live.close()
        reason = live_engine.safe_error(reason)
        log.warning("live session closed on %s (%s); reconnects so far: %s",
                    live.model, reason or "nessun motivo", self._live_reconnects)
        await self._send("voice.interrupted", reason="reconnect", during=str(self.vs.state))
        await self._send("voice.connection", status="reconnecting", reason=kind)
        self._out_text.clear()
        self._in_text = ""
        self._live_user_emitted = False
        self.vs.turn_active = False
        if self._live_used and kind not in ("auth", "quota"):
            while self._live_reconnects < 3:
                if self._dead:
                    return
                attempt = self._live_reconnects
                self._live_reconnects += 1
                try:
                    await asyncio.sleep(min(4, .5 * 2 ** attempt) + random.uniform(0, .2))
                    if self._dead:
                        return
                    async with self._live_connect_lock:
                        await asyncio.wait_for(self._start_live(model, handle), timeout=self.c.settings.live_connect_timeout)
                    await self._live_go(VoiceState.IDLE, VoiceState.LISTENING)
                    return
                except Exception as e:
                    log.warning("live reconnect failed: %s", live_engine.safe_error(e))
                    if live_engine.error_kind(str(e)) in ("auth", "quota"):
                        reason = str(e)
                        break
                    if handle:
                        handle = None  # stale/non-resumable token: rebuild from persisted history
        await self._fallback_live(reason)

    async def _live_tool_calls(self, calls: list[dict], bridge=None) -> None:
        """Execute a Live tool call through the SAME Toolbox as the text brain: guards unchanged."""
        from nodo.brain.tools import Toolbox

        rid = f"voice-live-{self.vs.id[:8]}-{self.vs.turn_number}"
        responses: list[tuple[str, str, dict]] = []
        bindings: list[dict] = []
        for c in calls:
            if self._dead or (bridge is not None and self._live is not bridge):
                return  # An old connection cannot start new actions after reconnect.
            await self._send("nodo.step", step=f"tool:{c['name']}", status="running", arguments=c["args"])
            try:
                if c["id"] and c["id"] in self._live_results:
                    result = self._live_results[c["id"]]
                elif c["name"] == THINK_DECL["name"]:
                    key = c.get("request_key") or self._think_key(c)
                    if self._pensa_error:
                        result = {"error": self._pensa_error}
                    elif key in self._think_results:
                        result = self._think_results[key]
                    else:
                        result = await self._live_think(c["args"], bindings, heard=c.get("user_text"))
                        self._think_results[key] = result
                        while len(self._think_results) > 8:
                            self._think_results.pop(next(iter(self._think_results)))
                        if "error" in result:
                            # A failed brain must not be retried inside the same turn: Gemini would
                            # otherwise re-call pensa forever. Tell it to answer honestly instead.
                            self._pensa_error = (str(result["error"]) +
                                                 " Non richiamare pensa in questo turno.")
                            result = {"error": self._pensa_error}
                elif c["name"] == "decide" and c["args"].get("approval_id") != self.vs.pending_approval_id:
                    result = {"error": "La decisione deve essere quella attualmente mostrata in questa sessione."}
                elif c["name"] == "decide" and not (
                    _APPROVE_RE.fullmatch(c.get("user_text", self._in_text).strip())
                    if c["args"].get("approve") is True
                    else _REJECT_RE.fullmatch(c.get("user_text", self._in_text).strip())
                ):
                    result = {"error": "Attendo un sì o un no chiaro per la proposta mostrata."}
                else:
                    with self._db() as sdb:
                        core = self.c.core(sdb)
                        conv = core._conversation(self.vs.conversation_id, "voice")
                        self.vs.conversation_id = conv.id
                        box = Toolbox(core, conv, rid, c.get("user_text", self._in_text), "voice")
                        result = await box.call(c["name"], c["args"])
                        bindings += box.bindings
                    if c["id"]:
                        self._live_results[c["id"]] = result
                    if c["name"] == "decide" and result.get("done"):
                        aid = self.vs.pending_approval_id
                        self.vs.pending_approval_id = None
                        await self._send("approval.resolved", approval_id=aid,
                                         status=result.get("decision"))
            except Exception as e:
                log.exception("live tool %s failed", c["name"])
                if c["name"] == THINK_DECL["name"]:
                    self._pensa_error = ("Il cervello principale non risponde adesso. Dillo a Gaetano in una "
                                         "frase onesta, senza inventare dati, e NON richiamare pensa in "
                                         "questo turno.")
                    result = {"error": self._pensa_error}
                else:
                    result = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            if c.get("generation", self._gen) != self._gen:
                # A reply completed after interruption must not speak over the new question; if WE stopped it,
                # Gemini still waits for an answer to this call
                if c.get("generation") in self._stopped_gens:
                    await self._close_call(c, bridge, "Gaetano ti ha fermata: non dire niente e ascoltalo.")
                return
            responses.append((c["id"], c["name"], result))
            await self._send("nodo.step", step=f"tool:{c['name']}", status="done",
                             ok="error" not in result)
            from nodo.brain.surfaces import surface_for
            if (card := surface_for(c["name"], result)) is not None:
                await self._send("ui.present", **card)
        if self._live is not None and (bridge is None or self._live is bridge):
            await self._live.tool_response(responses)
        if bridge is not None and self._live is not bridge:
            return
        for b in bindings:
            if b["approval_id"] != self.vs.pending_approval_id:
                self.vs.pending_approval_id = b["approval_id"]
                await self._set_state(VoiceState.WAITING_FOR_APPROVAL)
                await self._send("approval.required", **{k: v for k, v in b.items()
                                 if k in ("approval_id", "tool", "action", "level", "label", "params",
                                          "site", "preview_url", "pr_url")})

    async def _live_think(self, args: dict, bindings: list[dict], *, heard: str | None = None) -> dict:
        """«pensa»: the Claude brain (memory, register, tools, honesty guard) answers; Live only says it."""
        from nodo.brain.loop import Brain, BrainUnavailable
        from nodo.brain.quick_read import match_read
        from nodo.memory.mind import is_babble

        heard = (self._in_text if heard is None else heard).strip()
        asked = str(args.get("richiesta") or "").strip()
        if not asked and (not heard or is_babble(heard)):
            # a cough, the TV, a customer in Spanish: no brain run (each one costs 15-30 s and blocks the next)
            return self._quiet()
        if asked and (not heard or is_babble(heard) or (match_read(asked) and not match_read(heard))):
            # noise only, or the shop around a register question («y vale quali sono i sospesi»): Gemini's clean
            # version is the question, and an exact register read answers it at once without any model
            text = asked
        else:
            text = heard
            if asked and asked.lower() not in heard.lower() and not match_read(heard):
                text += f"\n(La voce ha capito così: {asked})"
        # Gemini already decided this was for Maria: the brain must answer, not choose «[silenzio]» because the
        # microphone also caught the shop around him (that silence was read aloud and the question came back
        # again and again: fifteen «sospesi» in ten minutes)
        rid = f"voice-think-{self.vs.id[:8]}-{self.vs.turn_number}"
        with self._db() as sdb:
            core = self.c.core(sdb)
            conv = core._conversation(self.vs.conversation_id, "voice")
            self.vs.conversation_id = conv.id
            brain = Brain(core, conv, rid, "voice", directed=True)
            started = time.monotonic()
            try:
                # the brain has its own deadline (brain_deadline_s): past it, it stops searching and answers with
                # what it knows. That last answer needs time too (15-20 s on the Claude program): cutting exactly
                # at the deadline turned every two-search question into «ci sto mettendo troppo»
                async with asyncio.timeout(self.c.settings.brain_deadline_s + 25):
                    await self._think_events(brain, text, bindings)
            except TimeoutError:
                log.warning("pensa took too long")
                with suppress(Exception):
                    sdb.commit()               # keep the record of which models were tried (the diagnosis)
                return {"error": "Ci sto mettendo troppo: dillo a Gaetano in una frase e chiedigli di riprovare, "
                                 "non inventare la risposta."}
            except BrainUnavailable as e:
                log.warning("pensa: no brain available: %s", str(e)[:300])
                with suppress(Exception):
                    sdb.commit()
                return {"error": "Il cervello non risponde adesso: dillo a Gaetano in una frase, non inventare."}
            except Exception:  # noqa: BLE001 - never let Gemini loop on a raw traceback
                log.exception("pensa failed")
                return {"error": "Il cervello ha avuto un problema tecnico: dillo a Gaetano in una frase, "
                                 "non inventare la risposta."}
            finally:
                log.info("pensa finished request=%s elapsed_ms=%.0f provider=%s model=%s tools=%s",
                         rid, (time.monotonic() - started) * 1000, brain.provider, brain.model, brain.tools_used)
            sdb.commit()
        if any(t.startswith("ledger_") for t in brain.tools_used):
            self._board_due = True                 # she just wrote in the register: the page is old
        if getattr(brain, "silent", False) or not (brain.text or "").strip():
            return self._quiet()
        return {"risposta": brain.text,
                "come": "Dilla con parole tue, naturale e breve, senza aggiungere niente che non c'è qui."}

    def _quiet(self) -> dict:
        """Nothing to say this turn: whatever Gemini says next is not relayed (no «silenzio» read aloud) and
        «pensa» cannot be called again until Gaetano speaks again."""
        self._live_muted = True
        self._pensa_error = "Non c'è niente da dire: resta zitta e NON richiamare pensa finché Gaetano non riparla."
        return {"risposta": "", "nota": "Niente da dire: non parlare."}

    def _babble_gate(self) -> bool:
        """Before Maria answers: was that «turn» really Gaetano talking to her? «de», «y», «Ich», «Bam Bam», a
        customer in Spanish… then she keeps quiet (her answer is dropped) and the line is not saved."""
        if self._babble_checked:
            return False
        self._babble_checked = True
        from nodo.memory.mind import is_babble
        heard = self._in_text.strip()
        if heard and is_babble(heard):
            self._live_muted = True
            self._babble = True
            self._log("voice.babble_ignored", {"text": heard[:60]})
            return True
        return False

    async def _greet(self, text: str) -> None:
        """Opening line in Maria's live voice. Not saved as something Gaetano said; her answer is kept."""
        owner = self.c.settings.owner_name
        line = (text or f"Ciao {owner}, eccomi. In cosa posso esserti utile oggi?").strip()[:200]
        if self._live_wanted and await self._ensure_live() and self._live is not None:
            try:
                await self._live.inject(f"[{owner} ha appena aperto NODO. Salutalo adesso, calda e naturale, "
                                        f"dicendo esattamente: «{line}». Poi ascolta.]", respond=True)
                return
            except live_engine.LiveDropped as e:
                await self._live_dropped(str(e))
        await self._send("tts.speak.request", text=line)     # no live voice: the panel says it with server TTS

    async def _think_events(self, brain, text: str, bindings: list[dict]) -> None:
        async for ev in brain.run(text):
            t, body = ev["type"], {k: v for k, v in ev.items() if k not in ("type", "request_id", "at")}
            if t == "step":
                await self._send("nodo.step", **body)
            elif t == "ui.present":
                await self._send("ui.present", **body)
            elif t == "approval.requested":
                bindings.append(body)

    async def _live_finalize_input(self) -> None:
        if self._in_text.strip() and not self._live_user_emitted:
            self._live_user_emitted = True
            await self._send("transcript.final", text=self._in_text.strip(), provider="gemini-live")

    async def _live_end_turn(self) -> None:
        vs = self.vs
        await self._live_finalize_input()
        vs.mark("model_done")
        text = "".join(self._out_text).strip()
        if self._babble:                   # noise, not a turn: nothing to keep
            self._in_text, text = "", ""
        self._babble = self._babble_checked = False
        from nodo.brain.loop import _SILENCE
        text = _VOICE_TAGS.sub("", text).strip()   # «<no speech>{pause}»: the model's markers are not words
        if _SILENCE.fullmatch(text):
            text = ""          # «[silenzio]» is a choice, not words: never keep it as something Maria said
        if vs.conversation_id and (self._in_text.strip() or text):
            rows = [(role, content) for role, content in (("user", self._in_text.strip()), ("nodo", text)) if content]
            conversation_id, request_id = vs.conversation_id, f"live-{vs.id[:8]}-{vs.turn_number}"

            def save() -> None:
                with self._db() as sdb:
                    # the night shift shares this database and this thread: never wait on its lock (it could not
                    # release it while we block), fail fast and try again after yielding to the event loop
                    if sdb.get_bind().dialect.name == "sqlite":
                        sdb.execute(text_sql("PRAGMA busy_timeout=200"))
                    # explicit, increasing times: on Windows two rows saved together can get the SAME timestamp,
                    # and the history then shows the answer before the question (the model loses the thread)
                    base = datetime.now(UTC)
                    for i, (role, content) in enumerate(rows):
                        sdb.add(Message(conversation_id=conversation_id, role=role, content=content,
                                        request_id=request_id,
                                        created_at=base + timedelta(milliseconds=i - len(rows) + 1)))
                    sdb.commit()

            try:
                await _retry_locked(save)
                from nodo.memory import mind
                mind.after_turn(self.c, conversation_id)          # Gemini Live never remembers by itself
            except Exception:
                log.exception("could not persist Live conversation turn")
                await self._send("error", where="live-memory", message="Non sono riuscito a salvare questo turno nel diario")
        if text:
            await self._send("nodo.response", text=text, conversation_id=vs.conversation_id,
                             intent="conversation", provider="gemini-live",
                             model=self._live.model if self._live else None, confidence="unknown",
                             agents=["live"])
        self._in_text = ""
        self._live_user_emitted = False
        self._out_text.clear()
        self._live_muted = False
        self._pensa_error = None
        self._barge_from = None
        self._live_reconnects = 0        # a healthy turn: the next drop deserves a fresh reconnect
        vs.mark("tts_done")
        vs.mark("turn_done")
        if self._board_due and not self._dead:
            asyncio.get_running_loop().call_later(0.5, lambda: asyncio.ensure_future(self._board_push()))
        self._persist_metrics(vs.interrupted)
        vs.turn_active = False
        if vs.state == VoiceState.WAITING_FOR_APPROVAL:
            pass                                    # a popup is open: stay there until it is answered
        elif vs.state != VoiceState.CLOSED:
            await self._live_go(VoiceState.LISTENING, VoiceState.IDLE, VoiceState.LISTENING)
        if vs.state != VoiceState.CLOSED:
            await self._send("voice.turn.done", state=str(vs.state), latency_ms=vs.latency_ms())

    # ------------------------------------------------------------- interrupt / approvals / close
    async def interrupt(self, reason: str = "user") -> None:
        """Real barge-in: cancels the outbound side of the active turn, drains TTS, then listens again."""
        vs = self.vs
        if vs.state == VoiceState.CLOSED:
            return
        if self._live is not None and (vs.turn_active or vs.state in (
            VoiceState.THINKING, VoiceState.ACTING, VoiceState.SPEAKING
        )):
            self._live_muted = True   # Gemini keeps generating internally; we just stop relaying audio
        during = str(vs.state)
        try:
            vs.interrupt()
        except Exception:
            return
        self._stopped_gens.add(self._gen)
        while len(self._stopped_gens) > 20:
            self._stopped_gens.discard(min(self._stopped_gens))
        self._gen += 1
        self._cancel_live_thoughts()
        self._drain_speech_q()
        self._log("voice.interrupt", {"reason": reason, "during": during})
        await self._send("voice.interrupted", reason=reason, during=during)
        await self._send_state()
        if vs.state == VoiceState.INTERRUPTED:
            await self._set_state(VoiceState.LISTENING)   # NODO stops and listens

    async def _answer_approval(self, approval_id: str, decision: str) -> None:
        vs = self.vs
        if approval_id != vs.pending_approval_id:
            await self._send("approval.ignored", reason="approval not bound to this session")
            return
        await self._resolve_pending(approve=decision == "approve", source="protocol")

    async def _resolve_pending(self, approve: bool, source: str) -> None:
        from nodo.night import approvals as A
        vs = self.vs
        approval_id = vs.pending_approval_id
        vs.pending_approval_id = None
        with self._db() as sdb:
            a = sdb.get(Approval, approval_id)
            if not a or a.status != "pending":
                await self._send("approval.ignored", reason="approval not pending")
                return
            tool, action = a.tool_name, a.action
            try:  # executes the stored action on approve (Phase 4), cleans up on reject
                out = await A.resolve(sdb, self.c, approval_id, approve, via="voice")
            except A.ApprovalError:
                await self._send("approval.ignored", reason="approval not pending")
                return
            status = out["status"]
        await self._send("approval.resolved", approval_id=approval_id, status=status, tool=tool, action=action)
        ack = out.get("message") or ("Confermato." if approve else "Va bene, non lo faccio.")
        await self._send("nodo.token", delta=ack)
        if self._live is not None:
            try:
                await self._live.inject(f"[ISTRUZIONE DI NODO, non dell'utente] {ack} "
                                        "Dillo a voce in una frase breve.", respond=True)
            except live_engine.LiveDropped as e:
                await self._live_dropped(str(e))
        else:
            await self._speak_text(ack)
        if vs.state == VoiceState.WAITING_FOR_APPROVAL:
            await self._set_state(VoiceState.LISTENING)

    async def close(self) -> None:
        self._gen += 1
        self._dead = True
        for task in [self._live_recovery, self._live_rotation, self._board_task, *self._live_tools.values()]:
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        if self._live is not None:
            live, self._live = self._live, None
            await live.close()
        self._drain_speech_q()
        if self._turn_task and not self._turn_task.done():
            with suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._turn_task), timeout=2)
        vs = self.vs
        if vs.state != VoiceState.CLOSED:
            try:
                vs.transition(VoiceState.CLOSED)
            except Exception:
                vs.state = VoiceState.CLOSED
        self._log("voice.session.closed", {"turns": vs.turn_number})
        with suppress(Exception):  # _send() refuses when _dead; emit directly for the last goodbye
            await self._emit(protocol.ev("voice.session.closed", vs.id, turn=vs.turn_number,
                                         seq=self._seq + 1, turns=vs.turn_number,
                                         latency_ms=vs.latency_ms()))

    # ------------------------------------------------------------- TTS streaming
    async def _speak_loop(self, gen: int, q: asyncio.Queue | None = None) -> None:
        q = q if q is not None else self._speech_q
        started = False
        while True:
            item = await q.get()
            if item is None or self._gen != gen or self._dead:
                break
            if not started:
                self.vs.mark("tts_started")
                try:
                    await self._send("tts.started", provider=self.c.tts.name)
                except InterruptRequested:
                    break
                started = True
            try:
                await self._speak_text(item, gen)
            except InterruptRequested:
                break

    async def _speak_text(self, text: str, gen: int | None = None) -> None:
        vs, tts = self.vs, self.c.tts
        if gen is not None and (self._gen != gen or self._dead):
            return
        if tts.location == "browser":
            vs.mark("tts_first_audio")
            await self._send("tts.speak", text=text)
            return
        try:
            if hasattr(tts, "synthesize_stream"):
                async for ch in tts.synthesize_stream(text, voice=self.c.settings.tts_voice,
                                                      language=vs.language):
                    if self._dead or (gen is not None and self._gen != gen):
                        return
                    vs.mark("tts_first_audio")
                    await self._send("tts.chunk", audio=protocol.encode_audio(ch.audio), mime=ch.mime,
                                     index=ch.index, final=ch.is_final)
            else:
                res = await tts.synthesize(text, self.c.settings.tts_voice, vs.language)
                if self._dead or (gen is not None and self._gen != gen):
                    return
                vs.mark("tts_first_audio")
                await self._send("tts.chunk", audio=protocol.encode_audio(res.audio), mime=res.mime,
                                 index=0, final=True)
        except Exception as e:
            if self._dead or (gen is not None and self._gen != gen):
                return
            await self._send("error", where="tts", message=f"{type(e).__name__}: {e}")

    def _drain_speech_q(self) -> None:
        while not self._speech_q.empty():
            with suppress(asyncio.QueueEmpty):
                self._speech_q.get_nowait()
        self._speech_q.put_nowait(None)

    # ------------------------------------------------------------- plumbing
    async def _set_state(self, to: VoiceState) -> None:
        try:
            self.vs.transition(to)
        except Exception:
            return
        self._log("voice.session.state", {"to": str(to)})
        await self._send_state()

    async def _send_state(self) -> None:
        await self._send("voice.session.state", state=str(self.vs.state))

    async def _send(self, type: str, **payload) -> None:
        if self._dead:
            raise InterruptRequested
        self._seq += 1
        try:
            await self._emit(protocol.ev(type, self.vs.id, turn=self.vs.turn_number, seq=self._seq, **payload))
        except InterruptRequested:
            raise
        except Exception:
            self._dead = True
            raise InterruptRequested

    def _log(self, type: str, payload: dict) -> None:
        try:
            with self._db() as s:
                org, _ = ensure_workspace(s)
                EventBus(s).emit(type, payload, organization_id=org.id)
        except Exception:
            log.debug("could not persist voice event %s", type)

    def _persist_metrics(self, interrupted: bool) -> None:
        vs = self.vs
        try:
            row = VoiceTurnMetric(
                session_id=vs.id, conversation_id=vs.conversation_id, request_id=self._request_id,
                turn_number=vs.turn_number, interrupted=interrupted, latency_ms=vs.latency_ms(),
                **{f"{k}_at": _parse_dt(vs.stamps.get(k)) for k in
                   ("turn_started", "speech_started", "speech_end", "transcript_partial_first",
                    "transcript_final", "intent_resolved", "context_built", "model_first_token",
                    "model_done", "tts_started", "tts_first_audio", "tts_done", "turn_done")})
            with self._db() as s:
                s.add(row)
        except Exception:
            log.debug("could not persist voice turn metrics")


def _parse_dt(v: str | None):
    return datetime.fromisoformat(v) if v else None
