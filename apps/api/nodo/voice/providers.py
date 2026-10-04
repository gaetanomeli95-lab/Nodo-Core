"""Concrete STT/TTS providers: fake (tests), browser (Web Speech API, free/local), OpenAI-compatible (cloud)."""
from __future__ import annotations

import time

import httpx

from nodo.config import Settings
from nodo.voice.base import AudioResult, SpeechToTextProvider, TextToSpeechProvider, Transcript


class FakeSTT:
    name, location = "fake", "local"

    def __init__(self, text: str = "fammi il punto su prosperya"):
        self.text, self.calls = text, 0

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        self.calls += 1
        return Transcript(self.text, language, 0.99, self.name)


class FakeTTS:
    name, location = "fake", "local"

    def __init__(self):
        self.spoken: list[str] = []

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        self.spoken.append(text)
        return AudioResult(b"RIFF-fake-wav:" + text.encode(), "audio/wav", self.name)


class BrowserSTT:
    """Speech is recognized in the browser (Web Speech API); audio never leaves the device. The client sends
    the transcript as text, so server-side transcription is a no-op that only validates the contract."""
    name, location = "browser", "browser"

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        if mime == "text/plain":
            return Transcript(audio.decode("utf-8"), language, None, self.name)
        raise RuntimeError("BrowserSTT expects the client to send text/plain transcripts")


class BrowserTTS:
    name, location = "browser", "browser"

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        # Returning the text as an SSML-free "speak request" lets the client use speechSynthesis.
        return AudioResult(text.encode("utf-8"), "text/plain", self.name)


class OpenAISpeech:
    """OpenAI-compatible /audio/transcriptions and /audio/speech (paid: only used outside FREE mode)."""
    name, location = "openai", "cloud"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", stt_model: str = "whisper-1",
                 tts_model: str = "tts-1", default_voice: str = "alloy"):
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.stt_model, self.tts_model, self.default_voice = stt_model, tts_model, default_voice

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        t0 = time.perf_counter()
        ext = {"audio/webm": "webm", "audio/wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg"}.get(mime, "webm")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.base_url}/audio/transcriptions", headers={"Authorization": f"Bearer {self.api_key}"},
                             data={"model": self.stt_model, "language": language},
                             files={"file": (f"audio.{ext}", audio, mime)})
            r.raise_for_status()
        return Transcript(r.json()["text"], language, None, self.name, int((time.perf_counter() - t0) * 1000))

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.base_url}/audio/speech", headers={"Authorization": f"Bearer {self.api_key}"},
                             json={"model": self.tts_model, "voice": voice or self.default_voice, "input": text[:4000],
                                   "response_format": "mp3"})
            r.raise_for_status()
        return AudioResult(r.content, "audio/mpeg", self.name)


def build_voice(s: Settings) -> tuple[SpeechToTextProvider, TextToSpeechProvider]:
    """FREE mode forbids paid speech providers: silently fall back to browser (and report it in /voice/config)."""
    from nodo.config import NodoMode
    paid_ok = s.mode != NodoMode.FREE or s.allow_paid_in_free_mode
    stt: SpeechToTextProvider
    tts: TextToSpeechProvider
    if s.stt_provider == "openai" and s.openai_api_key and paid_ok:
        stt = OpenAISpeech(s.openai_api_key, s.openai_base_url, default_voice=s.tts_voice)
    elif s.stt_provider == "fake":
        stt = FakeSTT()
    else:
        stt = BrowserSTT()
    if s.tts_provider == "openai" and s.openai_api_key and paid_ok:
        tts = OpenAISpeech(s.openai_api_key, s.openai_base_url, default_voice=s.tts_voice)
    elif s.tts_provider == "fake":
        tts = FakeTTS()
    else:
        tts = BrowserTTS()
    return stt, tts
