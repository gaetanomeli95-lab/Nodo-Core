"""Typed realtime voice protocol (ADR-005, Phase 2B) for WebSocket `/api/v1/voice/stream`.

All frames are JSON objects with a `type` discriminator; `audio.chunk` MAY also arrive as a binary frame
(opaque bytes, treated as raw audio in `audio_mime`). No arbitrary JSON blobs: every message validates against
this schema. `seq` numbers allow clients to detect gaps.
"""
from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, TypeAdapter

# ------------------------------------------------------------------ client -> server

class SessionStart(BaseModel):
    type: Literal["session.start"] = "session.start"
    conversation_id: str | None = None
    language: str = "it"
    audio_mime: str = "audio/webm"     # format of audio.chunk payloads (when server-side STT is used)
    client_stt: bool = True            # client does browser STT and sends transcripts instead of audio


class AudioChunk(BaseModel):
    type: Literal["audio.chunk"] = "audio.chunk"
    data: str                          # base64-encoded audio bytes
    mime: str | None = None            # overrides session audio_mime for this chunk


class SpeechEnd(BaseModel):
    type: Literal["speech.end"] = "speech.end"   # client detected end of speech (VAD / button release)


class TranscriptPartial(BaseModel):
    type: Literal["transcript.partial"] = "transcript.partial"
    text: str
    confidence: float | None = None


class TranscriptFinal(BaseModel):
    type: Literal["transcript.final"] = "transcript.final"
    text: str
    confidence: float | None = None


class Interrupt(BaseModel):
    type: Literal["interrupt"] = "interrupt"
    reason: str = "user"


class ApprovalAnswer(BaseModel):
    type: Literal["approval.answer"] = "approval.answer"
    approval_id: str
    decision: Literal["approve", "reject"]


class SessionEnd(BaseModel):
    type: Literal["session.end"] = "session.end"


Inbound = SessionStart | AudioChunk | SpeechEnd | TranscriptPartial | TranscriptFinal | Interrupt \
    | ApprovalAnswer | SessionEnd
INBOUND = TypeAdapter(Inbound)  # validates on the `type` literal


def parse_inbound(raw: str | dict) -> Inbound:
    return INBOUND.validate_python(raw if isinstance(raw, dict) else __import__("json").loads(raw))


# ------------------------------------------------------------------ server -> client

def ev(type: str, session_id: str, turn: int = 0, seq: int = 0, **payload) -> dict:
    """The canonical outbound envelope."""
    return {"type": type, "session_id": session_id, "turn": turn, "seq": seq, **payload}


# outbound types (names, payloads documented in docs/VOICE.md):
#   voice.session.started | voice.session.state | voice.session.closed
#   transcript.partial | transcript.final            (echoes/server-side STT)
#   nodo.thinking | nodo.intent | nodo.plan | nodo.context | nodo.step | agent.* | tool.*
#   nodo.token | nodo.sentence | nodo.response
#   tts.speak   (browser TTS: client speaks `text`)
#   tts.chunk   (server TTS: base64 `audio` + `mime`) | tts.started | tts.finished
#   voice.interrupted | voice.turn.done (with latency_ms)
#   approval.required | approval.resolved | approval.ignored
#   error

def encode_audio(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode_audio(b64: str) -> bytes:
    return base64.b64decode(b64.encode("ascii"), validate=False)
