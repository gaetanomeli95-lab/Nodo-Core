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
from datetime import UTC, datetime
from typing import Any

from nodo.app import Container, ensure_workspace
from nodo.core.events import EventBus
from nodo.db.models import Approval, EventLog, VoiceTurnMetric
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

    # ------------------------------------------------------------- inbound dispatch
    async def handle(self, msg) -> None:
        from nodo.voice.protocol import (
            ApprovalAnswer,
            AudioChunk,
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
        self._log("voice.session.started", {"language": vs.language})
        await self._send("voice.session.started", state=str(vs.state), conversation_id=vs.conversation_id,
                         stt=self.c.stt.name, tts=self.c.tts.name)

    async def _on_audio(self, msg: protocol.AudioChunk) -> None:
        vs = self.vs
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
                    if e["step"].startswith("agent:") and e["status"] == "running":
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

    # ------------------------------------------------------------- interrupt / approvals / close
    async def interrupt(self, reason: str = "user") -> None:
        """Real barge-in: cancels the outbound side of the active turn, drains TTS, then listens again."""
        vs = self.vs
        if vs.state == VoiceState.CLOSED:
            return
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
        vs = self.vs
        approval_id = vs.pending_approval_id
        vs.pending_approval_id = None
        with self._db() as sdb:
            a = sdb.get(Approval, approval_id)
            if not a or a.status != "pending":
                await self._send("approval.ignored", reason="approval not pending")
                return
            a.status = "approved" if approve else "rejected"
            a.resolved_at = datetime.now(UTC)
            sdb.add(EventLog(event_type=f"approval.{a.status}", request_id=a.request_id,
                             organization_id=a.organization_id,
                             payload={"approval_id": a.id, "tool": a.tool_name, "action": a.action,
                                      "via": "voice"}))
        await self._send("approval.resolved", approval_id=approval_id, status=a.status, tool=a.tool_name,
                         action=a.action)
        ack = "Confermato." if approve else "Va bene, non lo faccio."
        await self._send("nodo.token", delta=ack)
        await self._speak_text(ack)
        if vs.state == VoiceState.WAITING_FOR_APPROVAL:
            await self._set_state(VoiceState.LISTENING)

    async def close(self) -> None:
        self._gen += 1
        self._dead = True
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
