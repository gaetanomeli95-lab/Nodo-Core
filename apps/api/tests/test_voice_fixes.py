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


async def test_an_empty_200_from_a_provider_falls_back_instead_of_breaking_the_brain():
    """The «pensa» loop Devin found: a 200 with an empty body raised JSONDecodeError, not ProviderError, so the
    router never tried the next model. Now the provider reports it and ANY provider exception falls back."""
    import httpx

    from nodo.config import NodoMode
    from nodo.providers.base import FunctionSpec, ModelDescriptor, ProviderError, ToolTurn
    from nodo.providers.openai_compat import OpenAICompatProvider, gemini_descriptors
    from nodo.providers.router import ModelRouter, RouteRequest

    p = OpenAICompatProvider("gemini", "https://example.test/v1", "k", gemini_descriptors("gemini-3.5-flash"),
                             transport=httpx.MockTransport(lambda r: httpx.Response(200, text="")))
    spec = [FunctionSpec("f", "d", {"type": "object", "properties": {}})]
    with pytest.raises(ProviderError):
        await p.complete_tools("gemini-3.5-flash", [{"role": "user", "content": "ciao"}], spec)

    class Buggy:
        name = "buggy"
        def models(self):
            return [ModelDescriptor("buggy", "m", reasoning=5, coding=5, tier="free", tool_calling=True)]
        async def available(self):
            return True
        async def complete_tools(self, *a, **k):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class Good(Buggy):
        name = "good"
        def models(self):
            return [ModelDescriptor("good", "m", reasoning=3, coding=3, tier="free", tool_calling=True)]
        async def complete_tools(self, *a, **k):
            return ToolTurn(text="Marcello ci deve 30 euro.", tool_calls=[])

    r = ModelRouter([Buggy(), Good()], NodoMode.FREE)
    turn = await r.complete_tools([{"role": "user", "content": "x"}], spec, RouteRequest(purpose="brain"))
    assert turn.text.startswith("Marcello")


async def test_a_failed_pensa_is_not_retried_in_the_same_turn(container, engine, monkeypatch):
    from nodo.brain import loop
    from nodo.db.session import session_scope
    from nodo.voice.runtime import VoiceRuntime

    runs = []
    async def broken(self, text):
        runs.append(text)
        raise RuntimeError("boom")
        yield
    async def emit(event):
        pass
    monkeypatch.setattr(loop.Brain, "run", broken)
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    runtime._in_text = "quanto ho incassato oggi?"
    sent = []
    class Bridge:
        async def tool_response(self, responses):
            sent.extend(responses)
    runtime._live = Bridge()
    for i in range(3):                     # Gemini keeps asking: the brain runs ONCE
        await runtime._live_tool_calls([{"id": f"c{i}", "name": "pensa", "args": {"richiesta": "incasso"}}])
    assert len(runs) == 1
    assert all("Non richiamare pensa" in r[2]["error"] for r in sent)
    runtime._live = None
    await runtime.close()


def test_recording_a_model_call_does_not_take_the_database_lock(db):
    """The router records a failed model, then waits for the next (Claude: up to a minute). A flush there took
    SQLite's write lock for that whole minute: the voice could not save («database is locked») and froze."""
    from nodo.core.telemetry import Telemetry
    from nodo.db.models import InferenceRun
    from nodo.providers.router import InferenceRecord
    db.commit()
    Telemetry(db).record_inference(InferenceRecord("r", "gemini", "m", "brain", False, 0, 0, 10, 0.0, "failed",
                                                   "risposta non JSON", None))
    assert any(isinstance(o, InferenceRun) for o in db.new)           # pending, not written: no lock taken
    db.commit()
    assert db.query(InferenceRun).filter_by(error="risposta non JSON").count() == 1


async def test_saying_ferma_over_maria_stops_her_at_once(container, engine):
    from nodo.db.session import session_scope
    from nodo.voice.runtime import VoiceRuntime

    events = []
    async def emit(event):
        events.append(event)
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    class Bridge:
        async def close(self):
            pass
    runtime._live = Bridge()
    await runtime._on_live_event({"kind": "input", "text": "Basta che mi dici la giornata"})   # before she talks
    await runtime._on_live_event({"kind": "audio", "data": b"\x00\x00"})                      # Maria speaks
    assert not any(e["type"] == "voice.interrupted" for e in events)                          # «basta» was earlier
    await runtime._on_live_event({"kind": "input", "text": " ferma, ferma"})
    assert any(e["type"] == "voice.interrupted" and e["reason"] == "stop_word" for e in events)
    runtime._live = None
    await runtime.close()
