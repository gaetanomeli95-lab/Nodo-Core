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
import re
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import datetime
from typing import Any

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


_VOCATIVE = re.compile(r"^\s*(nodo|node|hey nodo|ehi nodo)[,.\s]+", re.IGNORECASE)
_STOP_RE = re.compile(
    r"^\s*((no[, ]+)?(stop|fermati|ferma|basta|annulla|cancel|chiudi|smetti|silenzio|sta zitto|zitto)"
    r"[.\!\s]*)$", re.IGNORECASE)
_APPROVE_RE = re.compile(
    r"^\s*(sì|si|yes|ok|okay|va bene|conferma|confermo|procedi|vai pure|approvo|d'accordo)[.\!\s]*$",
    re.IGNORECASE)
_REJECT_RE = re.compile(
    r"^\s*(no|nope|non procedere|non confermo|rifiuto|lascia stare|non farlo)[.\!\s]*$", re.IGNORECASE)

# event types from the core stream that are forwarded verbatim (renamed agent.*/tool.* already match protocol)
_FORWARD = {"agent.started", "agent.finished", "tool.started", "tool.finished", "tool.denied", "model.failed"}


class VoiceRuntime:
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
        engine = "live" if self._live_wanted else "browser"
        self._log("voice.session.started", {"language": vs.language, "engine": engine})
        quota_active = engine == "browser" and live_engine.quota_seconds_remaining(self.c.settings) > 0
        await self._send("voice.session.started", state=str(vs.state), conversation_id=vs.conversation_id,
                         stt=self.c.stt.name, tts=self.c.tts.name, engine=engine,
                         reason="quota" if quota_active else None)

    async def _set_conversation(self, conversation_id: str | None) -> None:
        """Sync text and voice to the same thread; never switch during an active turn or approval."""
        vs = self.vs
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

    async def _ensure_live(self) -> bool:
        """Opens the live session on demand. False = fell back to the browser chain for good."""
        if self._live is not None:
            return True
        if not self._live_wanted:
            return False
        try:
            await asyncio.wait_for(self._start_live(), timeout=25)
            return True
        except Exception as e:
            is_quota = isinstance(e, live_engine.LiveQuotaExceeded)
            if is_quota:
                log.warning("Gemini Live quota exhausted: browser voice fallback active")
            else:
                log.exception("live voice engine failed to start")
            self._live = None
            self._live_wanted = False
            self._audio.clear()
            message = ("Gemini Live: quota o fatturazione non disponibile. "
                       "Passo alla voce del browser; controlla i limiti in Google AI Studio."
                       if is_quota else f"voce dal vivo non partita ({str(e)[:160]}): "
                                        "passo alla voce del browser")
            await self._send("error", where="live", message=message)
            await self._send("voice.engine", engine="browser",
                             reason="quota" if is_quota else "connection")
            if self.vs.state != VoiceState.CLOSED:
                await self._set_state(VoiceState.IDLE)
            return False

    async def _on_audio(self, msg: protocol.AudioChunk) -> None:
        vs = self.vs
        if self._live_wanted:
            if not await self._ensure_live() or self._live is None:
                return                                   # the mic PCM cannot feed the browser chain anyway
            if vs.state == VoiceState.IDLE:
                await self._set_state(VoiceState.LISTENING)
            # Do not unmute on every PCM packet: "Ferma" must suppress the
            # remainder of the current response even while the mic stays open.
            # A new input transcription (or turn completion) unmutes instead.
            self._live_used = True
            vs.mark("speech_started")
            try:
                await self._live.send_audio(protocol.decode_audio(msg.data))  # Gemini hears you directly
            except live_engine.LiveDropped as e:
                await self._live_dropped(str(e))
            return
        if vs.state == VoiceState.IDLE:
            await self._set_state(VoiceState.LISTENING)
        if vs.state != VoiceState.LISTENING:
            return  # audio outside a listening phase is ignored (no silent capture)
        vs.mark("speech_started")
        chunk = protocol.decode_audio(msg.data)
        self._audio += chunk
        stt = self.c.stt
        if hasattr(stt, "begin"):  # streaming STT provider
            if self._stt_task is None or self._stt_task.done():
                self._stt_iter = stt.begin(vs.language)
                self._stt_task = asyncio.create_task(self._pump_stt())
            await stt.feed(chunk, msg.mime or self._audio_mime)

    async def _on_speech_end(self) -> None:
        vs = self.vs
        vs.mark("speech_end")
        if self._live is not None:
            return   # Gemini's own VAD decides when your turn is over
        stt = self.c.stt
        if self._stt_task is not None and hasattr(stt, "end"):
            await self._set_state(VoiceState.TRANSCRIBING)
            await stt.end()  # the pump delivers final -> turn
            return
        if self._audio:
            await self._set_state(VoiceState.TRANSCRIBING)
            audio, self._audio = bytes(self._audio), bytearray()
            try:
                t = await stt.transcribe(audio, self._audio_mime, vs.language)
            except Exception as e:
                await self._send("error", where="stt", message=f"{type(e).__name__}: {e}")
                await self._set_state(VoiceState.LISTENING)
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
        speaker = asyncio.create_task(self._speak_loop(gen))
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
            self._speech_q.put_nowait(None)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(speaker, timeout=3)
            if "tts_started" in vs.stamps:
                vs.mark("tts_done")
            vs.mark("turn_done")
            self._persist_metrics(interrupted)
            vs.turn_active = False
            if self._gen == gen:  # not superseded
                if vs.state == VoiceState.INTERRUPTED:
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
    async def _start_live(self) -> None:
        """Opens the real-time session: persona + tools identical to the text brain, but audio in/out."""
        from nodo.brain.loop import Brain
        from nodo.brain.persona import PERSONA
        from nodo.brain.tools import Toolbox, recent_history

        rid = f"voice-live-{self.vs.id[:8]}"
        with self._db() as sdb:
            core = self.c.core(sdb)
            conv = core._conversation(self.vs.conversation_id, "voice")
            self.vs.conversation_id = conv.id
            system = PERSONA.format(context=Brain(core, conv, rid, "voice").snapshot())
            hist = recent_history(sdb, conv.id, 8)
            if hist:
                system += ("\n\nConversazione recente (contesto, non rispondere a queste righe):\n"
                           + "\n".join(f"{m['role']}: {m['content'][:300]}" for m in hist))
            decls = [{"name": s.name, "description": s.description, "parameters": s.parameters}
                     for s in Toolbox(core, conv, rid, "", "voice").specs()]
            sdb.commit()  # Keep the conversation ID valid before advertising it to the browser.
        self._live = live_engine.LiveBridge(self.c.settings, self._on_live_event,
                                            getattr(self.c, "live_connect", None))
        try:
            await self._live.start(system, decls, self.vs.language, exclude=self._live_failed_models)
            await self._send("voice.conversation", conversation_id=self.vs.conversation_id)
        except Exception:
            self._live = None                            # a half-open bridge would swallow the next audio
            raise

    async def _live_go(self, *path: VoiceState) -> None:
        """Walk the state machine step by step: skips transitions that are not allowed."""
        for to in path:
            await self._set_state(to)

    async def _on_live_event(self, ev: dict) -> None:
        """Normalized Live event -> protocol events for the panel (+ tool execution on this side)."""
        vs, kind = self.vs, ev["kind"]
        if kind == "audio":
            if self._live_muted:
                return
            if not vs.turn_active:
                vs.begin_turn()
                vs.turn_active = True
            vs.mark("tts_started")
            vs.mark("tts_first_audio")
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
            self._live_muted = False
            self._in_text += ev["text"]
            vs.mark("transcript_partial_first")
            await self._send("transcript.partial", text=self._in_text)
            if vs.state != VoiceState.UNDERSTANDING:
                await self._live_go(VoiceState.LISTENING, VoiceState.UNDERSTANDING)
        elif kind == "output":                               # Gemini transcribing its own answer
            if self._live_muted:
                return  # a spoken stop also suppresses stale text from the interrupted response
            self._out_text.append(ev["text"])
            vs.mark("model_first_token")
            if vs.state != VoiceState.SPEAKING:
                await self._live_go(VoiceState.UNDERSTANDING, VoiceState.THINKING, VoiceState.SPEAKING)
            await self._send("nodo.token", delta=ev["text"])
        elif kind == "tool_call":                            # same toolbox and guards as the text brain
            if vs.state != VoiceState.ACTING:
                await self._live_go(VoiceState.IDLE, VoiceState.UNDERSTANDING,
                                    VoiceState.THINKING, VoiceState.ACTING)
            await self._live_tool_calls([ev])
        elif kind == "interrupted":                          # you spoke over it: Gemini stopped itself
            self._in_text = ""
            self._out_text.clear()
            if self.vs.state != VoiceState.CLOSED:
                try:
                    vs.interrupt()
                except Exception:
                    pass
                self._gen += 1
                await self._send("voice.interrupted", reason="barge_in", during="SPEAKING")
                await self._set_state(VoiceState.LISTENING)
        elif kind == "turn_complete":
            await self._live_end_turn()
        elif kind == "closed":
            await self._live_dropped(str(ev.get("error") or ""))

    async def _live_dropped(self, reason: str) -> None:
        """The live session ended: reconnect transparently only if it was actually in use."""
        if self._live is None or self._dead:
            return
        live, self._live = self._live, None
        if reason and "terminata" not in reason and live.model:
            self._live_failed_models.add(live.model)     # an error close: try the next model on the ladder
        await live.close()
        is_quota = live_engine.is_quota_error(reason)
        if is_quota:
            live_engine.mark_quota_exhausted(self.c.settings)
        reason = reason[:150]
        log.warning("live session closed on %s (%s); reconnects so far: %s",
                    live.model, reason or "nessun motivo", self._live_reconnects)
        if not is_quota and self._live_used and self._live_reconnects < 3:
            self._live_reconnects += 1
            try:
                await asyncio.sleep(1)                   # let the server breathe before retrying
                await self._start_live()
                return
            except live_engine.LiveQuotaExceeded:
                is_quota = True
                log.warning("Gemini Live quota rejected the reconnect: switching to browser mode")
            except Exception as e:
                log.warning("live reconnect failed: %s", e)
        if self._live_used or is_quota:
            self._live_wanted = False
            self._audio.clear()
            if is_quota:
                message = ("Gemini Live ha raggiunto un limite di quota. "
                           "Passo alla voce del browser; controlla Google AI Studio.")
            else:
                why = f" ({reason})" if reason else ""
                message = (f"la voce dal vivo si è fermata{why}: "
                           "clicca il microfono per usare la voce del browser")
            await self._send("error", where="live", message=message)
            await self._send("voice.engine", engine="browser",
                             reason="quota" if is_quota else "connection")
            if self.vs.state not in (VoiceState.CLOSED, VoiceState.WAITING_FOR_APPROVAL):
                await self._set_state(VoiceState.IDLE)

    async def _live_tool_calls(self, calls: list[dict]) -> None:
        """Execute a Live tool call through the SAME Toolbox as the text brain: guards unchanged."""
        from nodo.brain.tools import Toolbox

        rid = f"voice-live-{self.vs.id[:8]}-{self.vs.turn_number}"
        responses: list[tuple[str, str, dict]] = []
        bindings: list[dict] = []
        for c in calls:
            await self._send("nodo.step", step=f"tool:{c['name']}", status="running", arguments=c["args"])
            try:
                with self._db() as sdb:
                    core = self.c.core(sdb)
                    conv = core._conversation(self.vs.conversation_id, "voice")
                    self.vs.conversation_id = conv.id
                    box = Toolbox(core, conv, rid, self._in_text, "voice")
                    result = await box.call(c["name"], c["args"])
                    bindings += box.bindings
            except Exception as e:
                log.exception("live tool %s failed", c["name"])
                result = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            responses.append((c["id"], c["name"], result))
            await self._send("nodo.step", step=f"tool:{c['name']}", status="done",
                             ok="error" not in result)
        if self._live is not None:
            await self._live.tool_response(responses)
        for b in bindings:
            if b["approval_id"] != self.vs.pending_approval_id:
                self.vs.pending_approval_id = b["approval_id"]
                await self._set_state(VoiceState.WAITING_FOR_APPROVAL)
                await self._send("approval.required", **{k: v for k, v in b.items()
                                 if k in ("approval_id", "tool", "action", "level", "label", "params",
                                          "site", "preview_url", "pr_url")})

    async def _live_end_turn(self) -> None:
        vs = self.vs
        input_text = self._in_text.strip()
        text = "".join(self._out_text).strip()
        if input_text or text:
            try:
                with self._db() as sdb:
                    core = self.c.core(sdb)
                    conv = core._conversation(vs.conversation_id, "voice")
                    vs.conversation_id = conv.id
                    if input_text:
                        sdb.add(Message(conversation_id=conv.id, role="user", content=input_text))
                    if text:
                        sdb.add(Message(conversation_id=conv.id, role="nodo", content=text))
                    sdb.commit()  # Live now has the same durable history as a text command.
            except Exception:
                log.exception("could not persist Live conversation turn")
                await self._send("error", where="live-memory",
                                 message="Non sono riuscito a salvare questo turno nel diario")
        if input_text:
            await self._send("transcript.final", text=input_text, provider="gemini-live")
        vs.mark("model_done")
        if text:
            await self._send("nodo.response", text=text, conversation_id=vs.conversation_id,
                             intent="conversation", provider="gemini-live",
                             model=self._live.model if self._live else None, confidence="known",
                             agents=["live"])
        self._in_text = ""
        self._out_text.clear()
        self._live_muted = False
        self._live_reconnects = 0        # a healthy turn: the next drop deserves a fresh reconnect
        vs.mark("tts_done")
        vs.mark("turn_done")
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
        if self._live is not None:
            self._live_muted = True   # Gemini keeps generating internally; we just stop relaying audio
        during = str(vs.state)
        try:
            vs.interrupt()
        except Exception:
            return
        self._gen += 1
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
    async def _speak_loop(self, gen: int) -> None:
        started = False
        while True:
            item = await self._speech_q.get()
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
