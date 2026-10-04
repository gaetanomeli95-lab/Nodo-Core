"""Voice abstractions (ADR-005). STT/TTS are replaceable providers; VoiceSession is the state machine the UI
reflects. Wake word and turn detection are separate seams (not implemented in V0.1)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


@dataclass
class Transcript:
    text: str
    language: str = "it"
    confidence: float | None = None
    provider: str = ""
    duration_ms: int = 0


@dataclass
class AudioResult:
    audio: bytes
    mime: str
    provider: str


@runtime_checkable
class SpeechToTextProvider(Protocol):
    name: str
    location: str  # local | browser | cloud

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    name: str
    location: str

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult: ...


class WakeWordEngine(Protocol):  # Phase 5 seam (NODO LOCAL)
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class TurnDetector(Protocol):  # Phase 2 seam (streaming voice)
    def feed(self, audio_chunk: bytes) -> bool: ...


class VoiceState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    UNDERSTANDING = "UNDERSTANDING"
    THINKING = "THINKING"
    ACTING = "ACTING"
    SPEAKING = "SPEAKING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    ERROR = "ERROR"


TRANSITIONS: dict[VoiceState, set[VoiceState]] = {
    VoiceState.IDLE: {VoiceState.LISTENING, VoiceState.ERROR},
    VoiceState.LISTENING: {VoiceState.UNDERSTANDING, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.UNDERSTANDING: {VoiceState.THINKING, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.THINKING: {VoiceState.ACTING, VoiceState.SPEAKING, VoiceState.WAITING_FOR_APPROVAL, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.ACTING: {VoiceState.SPEAKING, VoiceState.WAITING_FOR_APPROVAL, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.SPEAKING: {VoiceState.IDLE, VoiceState.LISTENING, VoiceState.ERROR},  # LISTENING = barge-in
    VoiceState.WAITING_FOR_APPROVAL: {VoiceState.ACTING, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.ERROR: {VoiceState.IDLE},
}


class InvalidTransition(RuntimeError):
    pass


@dataclass
class VoiceSession:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str | None = None
    state: VoiceState = VoiceState.IDLE
    language: str = "it"
    interrupted: bool = False
    history: list[tuple[str, str]] = field(default_factory=list)

    def transition(self, to: VoiceState) -> None:
        if to not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state} -> {to}")
        self.history.append((datetime.now(UTC).isoformat(), str(to)))
        self.state = to

    def interrupt(self) -> None:
        """User barge-in / 'stop': always legal, returns to IDLE and flags in-flight speech for cancellation."""
        self.interrupted = True
        self.history.append((datetime.now(UTC).isoformat(), "INTERRUPT"))
        self.state = VoiceState.IDLE


class VoiceSessionStore:
    def __init__(self):
        self._sessions: dict[str, VoiceSession] = {}

    def create(self, conversation_id: str | None = None, language: str = "it") -> VoiceSession:
        s = VoiceSession(conversation_id=conversation_id, language=language)
        self._sessions[s.id] = s
        return s

    def get(self, session_id: str) -> VoiceSession | None:
        return self._sessions.get(session_id)
