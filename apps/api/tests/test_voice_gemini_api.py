"""Voice without Live: the Gemini standard API transcribes the microphone PCM and speaks the answer.
Verified against the real API on 11/10/2026 (3.5-flash reads audio/wav; the *-tts models answer audio/wav or
audio/L16); here the transport is faked so the tests stay offline."""
from __future__ import annotations

import base64
import json
import struct

import httpx
import pytest

from nodo.config import Settings
from nodo.voice.providers import (
    BrowserSTT,
    ChainSTT,
    ChainTTS,
    EdgeTTS,
    FakeTTS,
    GeminiSpeech,
    OpenAISpeech,
    build_voice,
    pcm_to_wav,
    wav_to_pcm,
)

WS = "/api/v1/voice/stream"
PCM_1S = bytes(16000 * 2)   # one second of 16 kHz silence (we only check the plumbing)


def _audio_wav_response(pcm: bytes, rate: int = 24000) -> dict:
    return {"candidates": [{"content": {"parts": [{"inlineData": {
        "mimeType": "audio/wav", "data": base64.b64encode(pcm_to_wav(pcm, rate)).decode()}}]}}]}


def _text_response(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeGemini:
    """Plays generativelanguage.googleapis.com: records every request, answers by model name."""

    def __init__(self, transcript: str = "fammi il punto su prosperya", quota: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.transcript, self.quota = transcript, quota

    def __call__(self, request: httpx.Request) -> httpx.Response:
        model = request.url.path.split("/models/")[1].split(":")[0]
        body = json.loads(request.content)
        self.calls.append((model, body))
        if self.quota:
            return httpx.Response(429, json={"error": {"message": "Resource has been exhausted (quota)"}})
        if model.endswith("-tts"):
            text = body["contents"][0]["parts"][0]["text"]
            return httpx.Response(200, json=_audio_wav_response(b"\x01\x00" * (len(text) * 40)))
        return httpx.Response(200, json=_text_response(self.transcript))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _speech(fake: FakeGemini) -> GeminiSpeech:
    return GeminiSpeech("k", "gemini-3.5-flash", "gemini-3.8-flash-lite-tts", "Kore", transport=fake.transport())


# ------------------------------------------------------------------------------------------------ provider
def test_wav_helpers_roundtrip_and_walk_chunks():
    pcm = bytes(range(256)) * 4
    wav = pcm_to_wav(pcm, 16000)
    assert wav[:4] == b"RIFF" and struct.unpack("<I", wav[24:28])[0] == 16000
    assert wav_to_pcm(wav) == (pcm, 16000)
    # an extra LIST chunk before data (some encoders add it) must not confuse the reader
    odd = wav[:12] + b"LIST" + struct.pack("<I", 4) + b"INFO" + wav[12:]
    assert wav_to_pcm(odd) == (pcm, 16000)


async def test_gemini_stt_wraps_pcm_in_wav_and_asks_for_a_bare_transcript():
    fake = FakeGemini(" «Ciao, che ore sono?» ")
    t = await _speech(fake).transcribe(PCM_1S, "audio/pcm;rate=16000", "it")
    assert t.text == "«Ciao, che ore sono?»" and t.provider == "gemini"
    model, body = fake.calls[0]
    assert model == "gemini-3.5-flash"
    part_text, part_audio = body["contents"][0]["parts"]
    assert "Trascrivi" in part_text["text"] and "it" in part_text["text"]
    assert part_audio["inlineData"]["mimeType"] == "audio/wav"
    assert base64.b64decode(part_audio["inlineData"]["data"])[:4] == b"RIFF"
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}   # thinking eats max tokens


async def test_gemini_tts_returns_raw_pcm_with_its_rate_and_the_live_voice():
    fake = FakeGemini()
    res = await _speech(fake).synthesize("Va bene.")
    assert res.mime == "audio/pcm;rate=24000" and res.audio[:2] == b"\x01\x00" and res.provider == "gemini"
    model, body = fake.calls[0]
    assert model == "gemini-3.8-flash-lite-tts"
    cfg = body["generationConfig"]
    assert cfg["responseModalities"] == ["AUDIO"]
    assert cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    chunks = [c async for c in _speech(fake).synthesize_stream("Va bene.")]
    assert len(chunks) == 1 and chunks[0].is_final and chunks[0].mime.startswith("audio/pcm")


async def test_gemini_quota_is_an_explicit_error_not_a_silent_failure():
    with pytest.raises(RuntimeError, match="quota"):
        await _speech(FakeGemini(quota=True)).transcribe(PCM_1S, "audio/pcm;rate=16000")


def test_build_voice_auto_prefers_gemini_when_the_key_exists():
    s = Settings(gemini_api_key="k", stt_provider="auto", tts_provider="auto")
    stt, tts = build_voice(s)
    assert isinstance(stt, GeminiSpeech) and stt.voice == s.live_voice
    # tts is a chain: gemini first, edge-tts as the free fallback (when the package is installed)
    assert isinstance(tts, (GeminiSpeech, ChainTTS))
    stt, tts = build_voice(Settings(gemini_api_key=None, groq_api_key=None, stt_provider="auto", tts_provider="auto"))
    assert isinstance(stt, BrowserSTT)
    stt, _ = build_voice(Settings(gemini_api_key="k", stt_provider="browser", tts_provider="browser"))
    assert isinstance(stt, BrowserSTT)   # an explicit choice is respected


def test_build_voice_auto_chains_the_free_fallbacks():
    stt, tts = build_voice(Settings(gemini_api_key="k", groq_api_key="g",
                                    stt_provider="auto", tts_provider="auto"))
    assert isinstance(stt, ChainSTT) and [p.name for p in stt.providers] == ["gemini", "groq"]
    assert stt.accepts_pcm   # both providers take the worklet's PCM frames
    assert isinstance(tts, ChainTTS) and [p.name for p in tts.providers] == ["gemini", "edge"]
    # groq alone is still a usable (free) server-side ear
    stt, _ = build_voice(Settings(gemini_api_key=None, groq_api_key="g",
                                  stt_provider="auto", tts_provider="auto"))
    assert isinstance(stt, OpenAISpeech) and stt.name == "groq"
    # tts falls back to edge alone when no Gemini key exists
    _, tts = build_voice(Settings(gemini_api_key=None, stt_provider="auto", tts_provider="auto"))
    assert isinstance(tts, EdgeTTS)


async def test_chain_tts_falls_over_to_the_next_provider():
    chain = ChainTTS([FakeTTS(fail=True), FakeTTS()])
    res = await chain.synthesize("ciao")
    assert res.audio == b"RIFF-fake-wav:ciao"
    chain = ChainTTS([FakeTTS(fail=True), FakeTTS(fail=True)])
    with pytest.raises(RuntimeError):
        await chain.synthesize("ciao")


# ------------------------------------------------------------------------------------------------ runtime
def _start(ws):
    ws.send_json({"type": "session.start", "language": "it", "client_stt": True})
    for _ in range(20):
        e = ws.receive_json()
        if e["type"] == "voice.session.started":
            return e
    raise AssertionError("no session.started")


def _until(ws, type_: str, limit: int = 300):
    events = []
    for _ in range(limit):
        e = ws.receive_json()
        events.append(e)
        if e["type"] == type_:
            return events
    raise AssertionError(f"never got {type_}: {[e['type'] for e in events]}")


def test_ws_api_engine_turns_microphone_pcm_into_a_spoken_answer(client, container):
    fake = FakeGemini("fammi il punto su prosperya")
    container.stt = container.tts = _speech(fake)
    with client.websocket_connect(WS) as ws:
        started = _start(ws)
        assert started["engine"] == "api" and started["stt"] == "gemini"
        frame = base64.b64encode(bytes(640)).decode()          # 20 ms of PCM, like the worklet
        for _ in range(60):                                      # 1.2 s of "speech"
            ws.send_json({"type": "audio.chunk", "data": frame, "mime": "audio/pcm;rate=16000"})
        ws.send_json({"type": "speech.end"})
        events = _until(ws, "voice.turn.done")
        types = [e["type"] for e in events]
        final = next(e for e in events if e["type"] == "transcript.final")
        assert final["text"] == "fammi il punto su prosperya" and final["provider"] == "gemini"
        assert "TRANSCRIBING" in [e.get("state") for e in events if e["type"] == "voice.session.state"]
        assert "tts.started" in types
        chunks = [e for e in events if e["type"] == "tts.chunk"]
        assert chunks and all(c["mime"] == "audio/pcm;rate=24000" for c in chunks) and chunks[-1]["final"]
        stt_calls = [m for m, _ in fake.calls if not m.endswith("-tts")]
        tts_calls = [m for m, _ in fake.calls if m.endswith("-tts")]
        assert len(stt_calls) == 1 and tts_calls                 # one transcription, one TTS call per sentence


def test_ws_api_engine_ignores_clicks_and_silence(client, container):
    fake = FakeGemini("")
    container.stt = container.tts = _speech(fake)
    with client.websocket_connect(WS) as ws:
        _start(ws)
        ws.send_json({"type": "audio.chunk", "data": base64.b64encode(bytes(640)).decode(), "mime": "audio/pcm;rate=16000"})
        ws.send_json({"type": "speech.end"})                     # 20 ms: a click, no API call at all
        ws.send_json({"type": "transcript.final", "text": "ciao"})
        _until(ws, "voice.turn.done")
        assert not [m for m, _ in fake.calls if not m.endswith("-tts")]
        frame = base64.b64encode(bytes(640)).decode()
        for _ in range(60):
            ws.send_json({"type": "audio.chunk", "data": frame, "mime": "audio/pcm;rate=16000"})
        ws.send_json({"type": "speech.end"})                     # real length, but Gemini hears nothing
        ws.send_json({"type": "transcript.final", "text": "ciao"})
        events = _until(ws, "voice.turn.done")
        assert len([m for m, _ in fake.calls if not m.endswith("-tts")]) == 1
        finals = [e["text"] for e in events if e["type"] == "transcript.final"]
        assert finals == ["ciao"]                                 # the empty transcript never became a turn


def test_voice_config_reports_the_fallback_engine(client, container):
    container.stt = container.tts = _speech(FakeGemini())
    cfg = client.get("/api/v1/voice/config").json()
    assert cfg["fallback_engine"] == "api" and cfg["stt"]["name"] == "gemini"
