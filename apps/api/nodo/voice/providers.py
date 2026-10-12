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
    """OpenAI-compatible /audio/transcriptions and /audio/speech (paid: only used outside FREE mode).
    Also serves Groq's free Whisper endpoint (base_url=https://api.groq.com/openai/v1)."""
    name, location = "openai", "cloud"
    accepts_pcm = True  # PCM worklet frames are wrapped into WAV before upload

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", stt_model: str = "whisper-1",
                 tts_model: str = "tts-1", default_voice: str = "alloy"):
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.stt_model, self.tts_model, self.default_voice = stt_model, tts_model, default_voice

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        t0 = time.perf_counter()
        if (mime or "").startswith("audio/pcm"):
            m = re.search(r"rate=(\d+)", mime)
            audio, mime = pcm_to_wav(audio, int(m.group(1)) if m else 16000), "audio/wav"
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


class EdgeTTS:
    """Free Microsoft neural voices via edge-tts (no API key): the TTS that survives a Google outage."""
    name, location = "edge", "cloud"
    streaming = True
    _VOICES = {"it": "it-IT-ElsaNeural", "en": "en-US-AriaNeural", "es": "es-ES-ElviraNeural",
               "fr": "fr-FR-DeniseNeural", "de": "de-DE-KatjaNeural"}

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        import edge_tts
        name = voice or self._VOICES.get(language.split("-")[0].lower(), "en-US-AriaNeural")
        buf = bytearray()
        async for part in edge_tts.Communicate(text[:3000], name).stream():
            if part["type"] == "audio":
                buf += part["data"]
        if not buf:
            raise RuntimeError("edge-tts returned no audio")
        return AudioResult(bytes(buf), "audio/mpeg", self.name)

    async def synthesize_stream(self, text: str, voice: str | None = None,
                                language: str = "it") -> AsyncIterator[SpeechChunk]:
        res = await self.synthesize(text, voice, language)
        yield SpeechChunk(res.audio, res.mime, 0, is_final=True)


class ChainSTT:
    """Tries server-side speech providers in order; a failure on one (quota, 5xx, timeout) moves to the next.
    Only server-side providers chain: the browser fallback is structural (it never saw the audio)."""
    location = "cloud"

    def __init__(self, providers: list[SpeechToTextProvider]):
        self.providers = providers
        self.name = "+".join(p.name for p in providers)
        self.accepts_pcm = all(getattr(p, "accepts_pcm", False) for p in providers)

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        last: Exception | None = None
        for p in self.providers:
            try:
                return await p.transcribe(audio, mime, language)
            except Exception as e:
                last = e
        raise last or RuntimeError("no stt provider")


class ChainTTS:
    """Same ladder for speech output (e.g. Gemini TTS -> Edge TTS -> …). Each provider emits its audio as a
    single chunk, so failover between providers never mixes formats mid-utterance."""
    location = "cloud"
    streaming = True

    def __init__(self, providers: list[TextToSpeechProvider]):
        self.providers = providers
        self.name = "+".join(p.name for p in providers)
        self.voice = getattr(providers[0], "voice", None)

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        last: Exception | None = None
        for p in self.providers:
            try:
                return await p.synthesize(text, voice, language)
            except Exception as e:
                last = e
        raise last or RuntimeError("no tts provider")

    async def synthesize_stream(self, text: str, voice: str | None = None,
                                language: str = "it") -> AsyncIterator[SpeechChunk]:
        last: Exception | None = None
        for p in self.providers:
            try:
                async for ch in p.synthesize_stream(text, voice, language):
                    yield ch
                return
            except Exception as e:
                last = e
        raise last or RuntimeError("no tts provider")


class GeminiSpeech:
    """Gemini standard API as the voice when Live is unavailable (quota, model, network). Free tier, quotas
    separate from Live. Measured on 11/10/2026: a short sentence synthesizes in ~2 s, a 3-second utterance
    transcribes in ~2-5 s. Not real-time: the client sends a whole utterance (PCM 16 kHz), the answer comes
    back sentence by sentence as PCM the existing player already handles. Same voice as Live (`live_voice`)."""
    name, location = "gemini", "cloud"
    streaming = True
    accepts_pcm = True           # the runtime may buffer the microphone worklet frames for us

    def __init__(self, api_key: str, text_model: str = "gemini-3.5-flash",
                 tts_model: str = "gemini-3.8-flash-lite-tts", voice: str = "Kore",
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 45.0):
        self.api_key, self.text_model, self.tts_model, self.voice = api_key, text_model, tts_model, voice
        self.base_url, self._transport, self._timeout = base_url.rstrip("/"), transport, timeout

    async def _generate(self, model: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self.base_url}/models/{model}:generateContent", json=body,
                             headers={"x-goog-api-key": self.api_key})
        if r.status_code >= 400:
            detail = ""
            try:
                detail = (r.json().get("error") or {}).get("message") or ""
            except ValueError:
                detail = r.text
            kind = "quota" if r.status_code == 429 else f"HTTP {r.status_code}"
            raise RuntimeError(f"Gemini {model}: {kind} {detail[:200]}".strip())
        return r.json()

    @staticmethod
    def _first_part(data: dict) -> dict:
        cands = data.get("candidates") or []
        parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
        return parts[0] if parts else {}

    async def transcribe(self, audio: bytes, mime: str, language: str = "it") -> Transcript:
        t0 = time.perf_counter()
        if (mime or "").startswith("audio/pcm"):
            m = re.search(r"rate=(\d+)", mime)
            audio, mime = pcm_to_wav(audio, int(m.group(1)) if m else 16000), "audio/wav"
        import base64
        prompt = (f"Trascrivi fedelmente il parlato (lingua: {language}). Rispondi SOLO con la trascrizione, "
                  "senza virgolette né commenti. Se non c'è parlato comprensibile rispondi con una riga vuota.")
        body = {"contents": [{"parts": [{"text": prompt},
                                        {"inlineData": {"mimeType": mime, "data": base64.b64encode(audio).decode()}}]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 600,
                                     "thinkingConfig": {"thinkingLevel": "minimal"}}}
        try:
            data = await self._generate(self.text_model, body)
        except RuntimeError as e:
            if "thinking" not in str(e).lower():
                raise
            del body["generationConfig"]["thinkingConfig"]          # older models: no thinking knob
            data = await self._generate(self.text_model, body)
        text = (self._first_part(data).get("text") or "").strip().strip('"').strip()
        return Transcript(text, language, None, self.name, int((time.perf_counter() - t0) * 1000))

    async def synthesize(self, text: str, voice: str | None = None, language: str = "it") -> AudioResult:
        body = {"contents": [{"parts": [{"text": text[:3000]}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": self.voice}}}}}
        data = await self._generate(self.tts_model, body)
        inline = self._first_part(data).get("inlineData") or {}
        if not inline.get("data"):
            raise RuntimeError(f"Gemini {self.tts_model}: nessun audio nella risposta")
        import base64
        raw, mime = base64.b64decode(inline["data"]), inline.get("mimeType") or "audio/L16;codec=pcm;rate=24000"
        if raw[:4] == b"RIFF":
            raw, rate = wav_to_pcm(raw)
        else:
            m = re.search(r"rate=(\d+)", mime)
            rate = int(m.group(1)) if m else 24000
        return AudioResult(raw, f"audio/pcm;rate={rate}", self.name)

    async def synthesize_stream(self, text: str, voice: str | None = None,
                                language: str = "it") -> AsyncIterator[SpeechChunk]:
        res = await self.synthesize(text, voice, language)
        yield SpeechChunk(res.audio, res.mime, 0, is_final=True)


def pcm_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    import struct
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + \
        struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2, channels * 2, 16) + \
        b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def wav_to_pcm(wav: bytes) -> tuple[bytes, int]:
    """16-bit PCM WAV -> (samples, rate). Walks the chunks instead of assuming a 44-byte header."""
    import struct
    rate, pos = 24000, 12
    while pos + 8 <= len(wav):
        cid, size = wav[pos:pos + 4], struct.unpack("<I", wav[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            rate = struct.unpack("<I", wav[pos + 12:pos + 16])[0]
        elif cid == b"data":
            return wav[pos + 8:pos + 8 + size], rate
        pos += 8 + size + (size & 1)
    return wav[44:], rate


def build_voice(s: Settings) -> tuple[SpeechToTextProvider, TextToSpeechProvider]:
    """FREE mode forbids paid speech providers: silently fall back to browser (and report it in /voice/config).
    `auto` = Gemini standard API when a Gemini key exists (the voice NODO keeps when Live is down), else browser."""
    from nodo.config import NodoMode
    paid_ok = s.mode != NodoMode.FREE or s.allow_paid_in_free_mode
    stt: SpeechToTextProvider
    tts: TextToSpeechProvider

    def gemini() -> GeminiSpeech:
        return GeminiSpeech(s.gemini_api_key or "", s.gemini_model, s.gemini_tts_model, s.live_voice)

    def groq_stt() -> OpenAISpeech | None:
        if not s.groq_api_key:
            return None
        p = OpenAISpeech(s.groq_api_key, "https://api.groq.com/openai/v1",
                         stt_model="whisper-large-v3-turbo")
        p.name = "groq"
        return p

    def edge_tts() -> EdgeTTS | None:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return None
        return EdgeTTS()

    if s.stt_provider == "local":           # offline Whisper on this PC (docs/VOICE_LOCAL.md)
        from nodo.voice.local import LocalWhisper
        stt = LocalWhisper(s.local_voice_dir, s.whisper_model, s.whisper_device, s.whisper_compute_type)
    elif s.stt_provider == "openai" and s.openai_api_key and paid_ok:
        stt = OpenAISpeech(s.openai_api_key, s.openai_base_url, default_voice=s.tts_voice)
    elif s.stt_provider == "fake":
        stt = FakeSTT()
    elif s.stt_provider == "groq" and s.groq_api_key:
        stt = groq_stt()
    elif s.stt_provider == "gemini" and s.gemini_api_key:
        stt = gemini()
    elif s.stt_provider == "auto":
        stts = [p for p in (gemini() if s.gemini_api_key else None, groq_stt()) if p]
        stt = stts[0] if len(stts) == 1 else (ChainSTT(stts) if stts else BrowserSTT())
    else:
        stt = BrowserSTT()
    if s.tts_provider == "local":
        from nodo.voice.local import LocalPiper
        tts = LocalPiper(s.local_voice_dir, s.piper_voice)
    elif s.tts_provider == "openai" and s.openai_api_key and paid_ok:
        tts = OpenAISpeech(s.openai_api_key, s.openai_base_url, default_voice=s.tts_voice)
    elif s.tts_provider == "fake":
        tts = FakeTTS()
    elif s.tts_provider == "edge" and edge_tts():
        tts = edge_tts()
    elif s.tts_provider == "gemini" and s.gemini_api_key:
        tts = gemini()
    elif s.tts_provider == "auto":
        ttss = [p for p in (gemini() if s.gemini_api_key else None, edge_tts()) if p]
        tts = ttss[0] if len(ttss) == 1 else (ChainTTS(ttss) if ttss else BrowserTTS())
    else:
        tts = BrowserTTS()
    return stt, tts
