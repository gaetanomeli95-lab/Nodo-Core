"""Concrete STT/TTS providers: fake (tests), browser (Web Speech API, free/local), OpenAI-compatible (cloud).

Phase 2 adds streaming variants: `FakeStreamingSTT`/`FakeStreamingTTS` are the deterministic doubles for the
realtime path; `BrowserTTS.synthesize_stream` yields the text itself (the browser synthesizes it); `OpenAISpeech`
satisfies the streaming protocol by yielding its single batch response as one chunk (honest, no fake streaming).
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator

import httpx

from nodo.config import Settings
from nodo.voice.base import (
    AudioResult,
    SpeechChunk,
    SpeechEvent,
    SpeechToTextProvider,
    TextToSpeechProvider,
    Transcript,
)


class FakeSTT:
    name, location = "fake", "local"

    def __init__(self, text: str = "fammi il punto su prosperya", fail: bool = False):
        self.text, self.calls, self.fail = text, 0, fail

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        self.calls += 1
        if self.fail:
            raise RuntimeError("fake stt failure")
        return Transcript(self.text, language, 0.99, self.name)


class FakeStreamingSTT(FakeSTT):
    """Deterministic streaming STT: each fed chunk reveals one more word of the scripted text as a
    `partial`; `end()` emits `final`. Lets tests exercise the full partial→final→turn pipeline."""
    streaming = True

    def __init__(self, text: str = "fammi il punto su prosperya", fail: bool = False):
        super().__init__(text, fail)
        self._events: asyncio.Queue[SpeechEvent | None] = asyncio.Queue()
        self._pos = 0
        self._language = "it"

    def begin(self, language: str = "it") -> AsyncIterator[SpeechEvent]:
        self._events = asyncio.Queue()
        self._pos, self._language = 0, language

        async def gen() -> AsyncIterator[SpeechEvent]:
            while True:
                ev = await self._events.get()
                if ev is None:
                    return
                yield ev
        return gen()

    async def feed(self, audio_chunk: bytes, mime: str = "audio/pcm") -> None:
        words = self.text.split(" ")
        self._pos = min(self._pos + 1, len(words))
        if self._pos < len(words):
            self._events.put_nowait(SpeechEvent("partial", " ".join(words[: self._pos]), self._language))

    async def end(self) -> None:
        if self.fail:
            self._events.put_nowait(SpeechEvent("error", error="fake stt failure"))
        else:
            self._events.put_nowait(SpeechEvent("final", self.text, self._language, 0.99))
        self._events.put_nowait(None)


class FakeTTS:
    name, location = "fake", "local"

    def __init__(self, fail: bool = False):
        self.spoken: list[str] = []
        self.fail = fail

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        self.spoken.append(text)
        if self.fail:
            raise RuntimeError("fake tts failure")
        return AudioResult(b"RIFF-fake-wav:" + text.encode(), "audio/wav", self.name)


class FakeStreamingTTS(FakeTTS):
    """Deterministic streaming TTS: emits one small 'wav' chunk per sentence-like segment, optionally with a
    delay so tests can interrupt mid-speech."""
    streaming = True

    def __init__(self, fail: bool = False, delay: float = 0.0):
        super().__init__(fail)
        self.delay = delay
        self.chunks_sent = 0

    async def synthesize_stream(self, text: str, voice: str | None = None,
                                language: str = "it") -> AsyncIterator[SpeechChunk]:
        self.spoken.append(text)
        if self.fail:
            raise RuntimeError("fake tts failure")
        segments = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()] or [text]
        for i, seg in enumerate(segments):
            if self.delay:
                await asyncio.sleep(self.delay)
            self.chunks_sent += 1
            yield SpeechChunk(b"RIFF-fake-wav:" + seg.encode(), "audio/wav", i, is_final=i == len(segments) - 1)


class BrowserSTT:
    """Speech is recognized in the browser (Web Speech API); audio never leaves the device. The client sends
    the transcript as text, so server-side transcription is a no-op that only validates the contract."""
    name, location = "browser", "browser"

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        if mime == "text/plain":
            return Transcript(audio.decode("utf-8"), language, None, self.name)
        raise RuntimeError("BrowserSTT expects the client to send text/plain transcripts")


class BrowserTTS:
    """Browser speechSynthesis. `synthesize_stream` yields the input text (already sentence-sized when fed
    from the orchestrator's sentence stream): the client speaks it incrementally, so audio starts before the
    whole answer exists."""
    name, location = "browser", "browser"
    streaming = True

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        # Returning the text as an SSML-free "speak request" lets the client use speechSynthesis.
        return AudioResult(text.encode("utf-8"), "text/plain", self.name)

    async def synthesize_stream(self, text: str, voice: str | None = None,
                                language: str = "it") -> AsyncIterator[SpeechChunk]:
        yield SpeechChunk(text.encode("utf-8"), "text/plain", 0, is_final=True)


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

    async def synthesize_stream(self, text: str, voice: str | None = None,
                                language: str = "it") -> AsyncIterator[SpeechChunk]:
        # The /audio/speech endpoint is batch; honest streaming contract: one final chunk. Per-sentence calls
        # from the runtime still give time-to-first-audio overlap on long answers.
        res = await self.synthesize(text, voice, language)
        yield SpeechChunk(res.audio, res.mime, 0, is_final=True)


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
