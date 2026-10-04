"""Voice routes (V0.1 push-to-talk): session lifecycle, transcribe, speak, interrupt.

Flow: client holds mic -> audio (or browser transcript) -> POST /voice/transcribe -> text ->
POST /command (SSE) -> text -> POST /voice/speak -> audio playback (or browser speechSynthesis).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from nodo.api.deps import auth, container
from nodo.app import Container
from nodo.voice.base import VoiceState

router = APIRouter(prefix="/api/v1/voice", dependencies=[Depends(auth)])


@router.get("/config")
def config(c: Container = Depends(container)):
    """Tells the client where speech is processed and which transports are available."""
    return {"stt": {"name": c.stt.name, "location": c.stt.location,
                    "streaming": bool(getattr(c.stt, "streaming", False))},
            "tts": {"name": c.tts.name, "location": c.tts.location, "voice": c.settings.tts_voice,
                    "streaming": bool(getattr(c.tts, "streaming", False))},
            "language": c.settings.default_language, "push_to_talk": True, "wake_word": False,
            "streaming": True, "ws_path": "/api/v1/voice/stream",
            "states": [str(s) for s in VoiceState],
            "audio_persistence": False}  # raw audio is never stored server-side (ADR-005)


@router.post("/sessions", status_code=201)
def create_session(conversation_id: str | None = None, c: Container = Depends(container)):
    s = c.voice_sessions.create(conversation_id, c.settings.default_language)
    return {"id": s.id, "state": s.state, "conversation_id": s.conversation_id}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, c: Container = Depends(container)):
    if s := c.voice_sessions.get(session_id):
        return {"id": s.id, "state": s.state, "interrupted": s.interrupted, "history": s.history[-20:]}
    raise HTTPException(404, "voice session not found")


@router.post("/sessions/{session_id}/state")
def set_state(session_id: str, state: VoiceState, c: Container = Depends(container)):
    s = c.voice_sessions.get(session_id)
    if not s:
        raise HTTPException(404, "voice session not found")
    try:
        s.transition(state)
    except Exception as e:
        raise HTTPException(409, str(e))
    return {"id": s.id, "state": s.state}


@router.post("/sessions/{session_id}/interrupt")
def interrupt(session_id: str, c: Container = Depends(container)):
    """REST fallback (push-to-talk client without WS): marks the session interrupted. The state lands in
    INTERRUPTED; the client should then move to LISTENING/IDLE via /state."""
    s = c.voice_sessions.get(session_id)
    if not s:
        raise HTTPException(404, "voice session not found")
    s.interrupt()
    return {"id": s.id, "state": s.state, "interrupted": True}


@router.post("/transcribe")
async def transcribe(file: UploadFile | None = File(default=None), text: str | None = Form(default=None),
                     language: str | None = Form(default=None), session_id: str | None = Form(default=None),
                     c: Container = Depends(container)):
    lang = language or c.settings.default_language
    if s := (c.voice_sessions.get(session_id) if session_id else None):
        if s.state == VoiceState.LISTENING:
            s.transition(VoiceState.UNDERSTANDING)
    if text is not None:   # browser-side recognition
        t = await c.stt.transcribe(text.encode("utf-8"), "text/plain", lang) if c.stt.location == "browser" \
            else None
        return {"text": text if t is None else t.text, "provider": "browser", "language": lang}
    if file is None:
        raise HTTPException(400, "provide an audio file or a text transcript")
    if c.stt.location == "browser":
        raise HTTPException(422, "server-side STT not configured (NODO_STT_PROVIDER=browser); send a text transcript")
    audio = await file.read()
    t = await c.stt.transcribe(audio, file.content_type or "audio/webm", lang)
    return {"text": t.text, "provider": t.provider, "language": t.language, "duration_ms": t.duration_ms}


@router.post("/speak")
async def speak(text: str = Form(...), voice: str | None = Form(default=None), language: str | None = Form(default=None),
                c: Container = Depends(container)):
    res = await c.tts.synthesize(text, voice or c.settings.tts_voice, language or c.settings.default_language)
    return Response(content=res.audio, media_type=res.mime, headers={"X-NODO-TTS-Provider": res.provider})
