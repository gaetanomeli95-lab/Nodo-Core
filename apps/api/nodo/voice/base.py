"""Voice abstractions (ADR-005). STT/TTS are replaceable providers; VoiceSession is the state machine the UI
reflects. Phase 2 adds streaming protocols, extended lifecycle and per-turn latency stamps.

Raw audio is never persisted: providers receive it in-process only.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

# ------------------------------------------------------------------ transcripts / audio

@dataclass
class Transcript:
    text: str
    language: str = "it"
    confidence: float | None = None
    provider: str = ""
    duration_ms: int = 0
    is_final: bool = True


@dataclass
class AudioResult:
    audio: bytes
    mime: str
    provider: str


@dataclass
class SpeechEvent:
    """An event yielded by a streaming STT provider."""
    kind: Literal["partial", "final", "speech_started", "speech_ended", "error"]
    text: str = ""
    language: str = "it"
    confidence: float | None = None
    error: str | None = None


@dataclass
class SpeechChunk:
    """A chunk yielded by a streaming TTS provider (audio bytes, or text for browser-side synthesis)."""
    audio: bytes
    mime: str
    index: int = 0
    is_final: bool = False


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Batch STT: one audio buffer -> one transcript (V0.1 contract, kept for REST /voice/transcribe)."""
    name: str
    location: str  # local | browser | cloud

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript: ...


@runtime_checkable
class StreamingSpeechToTextProvider(Protocol):
    """Streaming STT (Phase 2C): receives audio chunks, yields partial/final transcript events.

    Implementations may buffer internally; partial events let the UI show live recognition while the
    orchestrator only executes when the turn is considered final (`is_final` / `speech_ended`).
    """
    name: str
    location: str
    streaming: bool = True

    def begin(self, language: str = "it") -> AsyncIterator[SpeechEvent]:
        """Returns an async iterator; audio is fed via `feed()`; `end()` closes the stream."""
        ...

    async def feed(self, audio_chunk: bytes, mime: str = "audio/pcm") -> None: ...

    async def end(self) -> None: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Batch TTS: one text -> one audio buffer (V0.1 contract, kept for REST /voice/speak)."""
    name: str
    location: str

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult: ...


@runtime_checkable
class StreamingTextToSpeechProvider(Protocol):
    """Streaming TTS (Phase 2F): incremental text -> incremental audio, so speech starts before the
    full answer is generated. Providers with `location == "browser"` may yield the text itself as
    `SpeechChunk.audio` (utf-8, mime "text/plain"): the client synthesizes it with speechSynthesis."""
    name: str
    location: str
    streaming: bool = True

    def synthesize_stream(self, text: str, voice: str | None = None,
                          language: str = "it") -> AsyncIterator[SpeechChunk]:
        """Yields audio chunks in playback order. `is_final` on the last chunk."""
        ...


class WakeWordEngine(Protocol):  # Phase 5 seam (NODO LOCAL) — always-on listening must run on-device
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class TurnDetector(Protocol):  # Phase 2D seam: decides when the user's turn is over
    def feed(self, audio_chunk: bytes) -> bool: ...
    def should_end_turn(self) -> bool: ...


# ------------------------------------------------------------------ session state machine

class VoiceState(StrEnum):
    IDLE = "IDLE"                    # connected, nothing happening
    CONNECTING = "CONNECTING"        # transport opening
    LISTENING = "LISTENING"          # microphone open, capturing the user
    TRANSCRIBING = "TRANSCRIBING"    # STT running (streaming partials or batch finalize)
    UNDERSTANDING = "UNDERSTANDING"  # intent classification + context
    THINKING = "THINKING"            # plan / synthesis in progress
    ACTING = "ACTING"                # agents / tools executing
    SPEAKING = "SPEAKING"            # TTS playback in progress
    INTERRUPTED = "INTERRUPTED"      # user barge-in or explicit stop
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"  # an action needs confirmation
    ERROR = "ERROR"
    CLOSED = "CLOSED"


TRANSITIONS: dict[VoiceState, set[VoiceState]] = {
    VoiceState.CONNECTING: {VoiceState.IDLE, VoiceState.CLOSED, VoiceState.ERROR},
    VoiceState.IDLE: {VoiceState.LISTENING, VoiceState.UNDERSTANDING, VoiceState.CLOSED, VoiceState.ERROR},
    VoiceState.LISTENING: {VoiceState.TRANSCRIBING, VoiceState.UNDERSTANDING, VoiceState.IDLE,
                           VoiceState.INTERRUPTED, VoiceState.CLOSED, VoiceState.ERROR},
    VoiceState.TRANSCRIBING: {VoiceState.UNDERSTANDING, VoiceState.LISTENING, VoiceState.IDLE,
                              VoiceState.INTERRUPTED, VoiceState.CLOSED, VoiceState.ERROR},
    VoiceState.UNDERSTANDING: {VoiceState.THINKING, VoiceState.LISTENING, VoiceState.IDLE,
                               VoiceState.INTERRUPTED, VoiceState.CLOSED, VoiceState.ERROR},
    VoiceState.THINKING: {VoiceState.ACTING, VoiceState.SPEAKING, VoiceState.WAITING_FOR_APPROVAL,
                          VoiceState.LISTENING, VoiceState.IDLE, VoiceState.INTERRUPTED,
                          VoiceState.CLOSED, VoiceState.ERROR},
    VoiceState.ACTING: {VoiceState.SPEAKING, VoiceState.THINKING, VoiceState.WAITING_FOR_APPROVAL,
                        VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.CLOSED, VoiceState.ERROR},
    VoiceState.SPEAKING: {VoiceState.IDLE, VoiceState.LISTENING, VoiceState.INTERRUPTED,
                          VoiceState.CLOSED, VoiceState.ERROR},  # LISTENING/INTERRUPTED = barge-in
    VoiceState.WAITING_FOR_APPROVAL: {VoiceState.ACTING, VoiceState.UNDERSTANDING, VoiceState.LISTENING,
                                      VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.CLOSED,
                                      VoiceState.ERROR},
    VoiceState.INTERRUPTED: {VoiceState.LISTENING, VoiceState.IDLE, VoiceState.CLOSED, VoiceState.ERROR},
    VoiceState.ERROR: {VoiceState.IDLE, VoiceState.LISTENING, VoiceState.CLOSED},
    VoiceState.CLOSED: set(),
}

INTERRUPTABLE = {VoiceState.LISTENING, VoiceState.TRANSCRIBING, VoiceState.UNDERSTANDING, VoiceState.THINKING,
                 VoiceState.ACTING, VoiceState.SPEAKING, VoiceState.WAITING_FOR_APPROVAL}


class InvalidTransition(RuntimeError):
    pass


# latency stamps recorded for every voice turn (Phase 2M)
TURN_STAMPS = ("turn_started", "speech_started", "speech_end", "transcript_partial_first", "transcript_final",
               "intent_resolved", "context_built", "model_first_token", "model_done",
               "tts_started", "tts_first_audio", "tts_done", "turn_done")


@dataclass
class VoiceSession:
    """Server-side voice session. Identity is stable; every transition is recorded in `history`
    ([timestamp, state]) and surfaced as `voice.session.state` events by the runtime."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str | None = None
    state: VoiceState = VoiceState.IDLE
    language: str = "it"
    interrupted: bool = False               # current turn was interrupted (resets at next turn)
    turn_number: int = 0
    turn_active: bool = False
    pending_approval_id: str | None = None  # an approval is only answerable while this is set (2J)
    stamps: dict[str, str] = field(default_factory=dict)  # TURN_STAMPS -> ISO time, per turn
    history: list[tuple[str, str]] = field(default_factory=list)

    def transition(self, to: VoiceState) -> None:
        if to not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state} -> {to}")
        self._enter(to)

    def _enter(self, to: VoiceState) -> None:
        self.history.append((datetime.now(UTC).isoformat(), str(to)))
        self.state = to

    def interrupt(self) -> None:
        """Barge-in / explicit stop: always legal. In-flight generation and playback must check
        `interrupted`; the state becomes INTERRUPTED (reachable from every active state)."""
        if self.state == VoiceState.CLOSED:
            raise InvalidTransition("CLOSED -> INTERRUPTED")
        self.interrupted = True
        self.history.append((datetime.now(UTC).isoformat(), "INTERRUPT"))
        self._enter(VoiceState.INTERRUPTED)

    def begin_turn(self) -> int:
        """Marks a new turn: resets the interrupt flag and latency stamps, returns the turn number."""
        self.turn_number += 1
        self.interrupted = False
        self.stamps = {"turn_started": _now()}
        return self.turn_number

    def mark(self, stamp: str) -> None:
        self.stamps.setdefault(stamp, _now())

    def latency_ms(self) -> dict[str, int]:
        """Computed latency metrics for the finished turn (all durations in ms)."""
        def d(a: str | None, b: str | None) -> int | None:
            if not a or not b:
                return None
            return int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() * 1000)
        s = self.stamps
        ref_start = s.get("speech_end") or s.get("transcript_final") or s.get("turn_started")
        out = {"stt": d(s.get("speech_end"), s.get("transcript_final")),
               "context": d(s.get("intent_resolved"), s.get("context_built")),
               "model_ttft": d(s.get("context_built") or s.get("intent_resolved"), s.get("model_first_token")),
               "model_total": d(s.get("model_first_token"), s.get("model_done")),
               "tts_tfa": d(s.get("transcript_final"), s.get("tts_first_audio")),
               "tts_total": d(s.get("tts_started"), s.get("tts_done")),
               "total": d(ref_start, s.get("turn_done") or s.get("tts_done"))}
        return {k: v for k, v in out.items() if v is not None}


class VoiceSessionStore:
    """In-memory registry (single-process Phase 2). A persistent/distributed store is a Phase 7 concern."""
    def __init__(self):
        self._sessions: dict[str, VoiceSession] = {}

    def create(self, conversation_id: str | None = None, language: str = "it") -> VoiceSession:
        s = VoiceSession(conversation_id=conversation_id, language=language)
        self._sessions[s.id] = s
        return s

    def get(self, session_id: str) -> VoiceSession | None:
        return self._sessions.get(session_id)


def _now() -> str:
    return datetime.now(UTC).isoformat()
