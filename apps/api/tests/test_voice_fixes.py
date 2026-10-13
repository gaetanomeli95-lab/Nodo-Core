"""Fixes from the voice review: answers spoken to the end, a leftover stop marker never mutes the next turn,
ears open while a confirmation waits, register updates all-or-nothing."""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest


class SlowTTS:
    name, location, delay = "slow", "local", 1.1

    async def synthesize(self, text, voice=None, language=None):
        await asyncio.sleep(self.delay)
        return SimpleNamespace(audio=b"\x00\x00", mime="audio/wav")


async def test_long_answer_is_spoken_to_the_end_and_the_next_turn_is_not_muted(container, engine, monkeypatch):
    from nodo.db.session import session_scope
    from nodo.voice.runtime import VoiceRuntime

    events = []
    async def emit(event):
        events.append(event)
    async def pipeline(self, text, gen):
        for s in ("Uno.", "Due.", "Tre."):
            self._speech_q.put_nowait(s)
    monkeypatch.setattr(VoiceRuntime, "_pipeline", pipeline)
    container.tts = SlowTTS()
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    await runtime.run_turn("raccontami la giornata")          # 3 sentences x 1.1 s: more than the old 3 s cap
    assert len([e for e in events if e["type"] == "tts.chunk"]) == 3
    runtime._drain_speech_q()                                  # an interruption leaves a stop marker behind
    container.tts.delay = 0
    events.clear()
    await runtime.run_turn("e poi?")
    assert len([e for e in events if e["type"] == "tts.chunk"]) == 3
    await runtime.close()


async def test_ears_stay_open_while_a_confirmation_waits(container, engine):
    from nodo.db.session import session_scope
    from nodo.voice.base import VoiceState
    from nodo.voice.protocol import AudioChunk
    from nodo.voice.runtime import VoiceRuntime

    async def emit(event):
        pass
    container.settings.voice_engine = "precise"
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    runtime._live_wanted = False
    vs = runtime.vs
    vs.transition(VoiceState.LISTENING), vs.transition(VoiceState.UNDERSTANDING)
    vs.transition(VoiceState.THINKING), vs.transition(VoiceState.WAITING_FOR_APPROVAL)
    vs.pending_approval_id = "proposta-1"
    await runtime.handle(AudioChunk(data="AAEAAg==", mime="audio/webm"))
    assert vs.state == VoiceState.LISTENING and vs.pending_approval_id == "proposta-1"
    await runtime.close()


def test_register_update_is_all_or_nothing(db):
    from nodo.app import ensure_workspace
    from nodo.ledger import Ledger
    org = ensure_workspace(db)[0].id
    led = Ledger(db, org, "CF Materassi", today=date(2026, 10, 13))
    e = led.add(kind="vendita", item="materasso Arianna H20", size="160x190", amount=110, counterpart="Rossi")
    with pytest.raises(ValueError):
        led.update(e, item="rete a doghe", amount="centodieci", status="fatto")
    with pytest.raises(ValueError):
        led.update(e, item="rete a doghe", status="boh")
    assert e.item == "materasso Arianna H20" and e.amount == 110      # nothing half-saved
    assert led.update(e, amount="120,50")                             # a comma decimal is fine
    assert e.amount == 120.5


def test_open_mic_pcm_needs_no_decoder():
    np = pytest.importorskip("numpy")
    from nodo.voice.local import pcm_samples
    x = (np.sin(np.linspace(0, 200, 32000)) * 16000).astype("<i2")
    assert pcm_samples(x.tobytes(), 16000).shape == (32000,)
    assert pcm_samples(x.tobytes(), 32000).shape == (16000,)      # resampled to Whisper's 16 kHz
    assert abs(float(pcm_samples(x.tobytes()).max()) - 16000 / 32768) < 0.01


async def test_voice_selftest_catches_a_broken_decoder(monkeypatch):
    from nodo.voice import local, selftest
    from nodo.voice.base import AudioResult, Transcript
    from nodo.voice.providers import pcm_to_wav

    class Piper:
        name = "piper"
        def __init__(self, *a): pass
        def status(self): return {"ready": True}
        async def synthesize(self, text, voice=None, language="it"):
            return AudioResult(pcm_to_wav(b"\x00\x01" * 800, 16000), "audio/wav", "piper")

    class Whisper:
        name, broken = "faster-whisper", False
        def __init__(self, *a): pass
        def status(self): return {"ready": True}
        async def transcribe(self, audio, mime, language="it"):
            if Whisper.broken and mime == "audio/wav":
                raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
            return Transcript("Ciao Gaetano, sono Maria. Prova audio riuscita.", "it", None, self.name)

    monkeypatch.setattr(local, "LocalPiper", Piper)
    monkeypatch.setattr(local, "LocalWhisper", Whisper)
    assert (await selftest.run(".local-voice", "base", "it_IT-paola-medium"))[0]
    Whisper.broken = True
    ok, msg = await selftest.run(".local-voice", "base", "it_IT-paola-medium")
    assert not ok and "PyAV" in msg
