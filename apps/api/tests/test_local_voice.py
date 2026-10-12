"""Offline speech stack: no network calls, lazy assets, and no paid inference."""
from __future__ import annotations

import io
import types
import wave

import pytest

from nodo.config import Settings
from nodo.voice.local import MAX_AUDIO_BYTES, LocalPiper, LocalWhisper, voice_files
from nodo.voice.providers import build_voice


def test_voice_files_resolve_to_explicit_local_assets(tmp_path):
    whisper, piper = voice_files(str(tmp_path), "base", "it_IT-paola-medium")
    assert whisper == tmp_path / "whisper" / "base"
    assert piper == tmp_path / "piper" / "it_IT-paola-medium.onnx"


def test_local_providers_fail_closed_without_setup(tmp_path):
    stt = LocalWhisper(str(tmp_path))
    tts = LocalPiper(str(tmp_path))
    assert stt.status()["ready"] is False
    assert tts.status()["ready"] is False
    with pytest.raises(RuntimeError, match="installato"):
        stt._load()
    with pytest.raises(RuntimeError, match="installato"):
        tts._load()


def test_build_voice_chooses_offline_stt_tts_in_free_mode(tmp_path):
    s = Settings(stt_provider="local", tts_provider="local", mode="FREE",
                 local_voice_dir=str(tmp_path), whisper_model="base", piper_voice="it_IT-paola-medium",
                 gemini_api_key=None)
    stt, tts = build_voice(s)
    assert isinstance(stt, LocalWhisper) and isinstance(tts, LocalPiper)
    assert stt.location == tts.location == "local"


async def test_whisper_transcribe_uses_memory_audio_and_single_loaded_model(monkeypatch, tmp_path):
    calls: list[dict] = []

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            assert len(audio) == 1600
            return [types.SimpleNamespace(text=" Ciao"), types.SimpleNamespace(text=" NODO.")], None

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        types.SimpleNamespace(decode_audio=lambda data, sampling_rate: [0.0] * (sampling_rate // 10)))
    # The real engine is lazy; injecting a fake model means no package/model download.
    stt = LocalWhisper(str(tmp_path))
    stt._model = FakeModel()
    result = await stt.transcribe(b"fake-webm", "audio/webm;codecs=opus", "it-IT")
    assert result.text == "Ciao NODO."
    assert result.provider == "faster-whisper"
    assert calls[0]["language"] == "it"
    assert calls[0]["beam_size"] == 2


async def test_piper_synthesis_is_real_wav_container_without_network(tmp_path):
    class FakeVoice:
        def synthesize_wav(self, text, wav):
            assert text == "Ciao, posso aiutarti."
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\0" * 200)

    tts = LocalPiper(str(tmp_path))
    tts._voice = FakeVoice()
    audio = await tts.synthesize("Ciao, posso aiutarti.")
    assert audio.mime == "audio/wav" and audio.audio[:4] == b"RIFF"
    with wave.open(io.BytesIO(audio.audio), "rb") as wav:
        assert wav.getframerate() == 22050
        assert wav.getnframes() == 100


async def test_audio_upload_size_is_capped(tmp_path):
    stt = LocalWhisper(str(tmp_path))
    with pytest.raises(ValueError, match="12 MB"):
        await stt.transcribe(b"x" * (MAX_AUDIO_BYTES + 1), "audio/webm")


async def test_local_whisper_takes_the_open_microphone_pcm(monkeypatch):
    """Open-mic conversation (engine "api") streams raw PCM: local Whisper wraps it into WAV itself."""
    from nodo.voice.local import LocalWhisper
    w = LocalWhisper("/nonexistent")
    seen = {}
    monkeypatch.setattr(w, "_transcribe", lambda audio, lang: seen.setdefault("head", audio[:4]) and "ciao")
    assert w.accepts_pcm
    out = await w.transcribe(b"\x00\x01" * 1600, "audio/pcm;rate=16000")
    assert seen["head"] == b"RIFF" and out.text == "ciao"
