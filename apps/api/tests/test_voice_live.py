"""Gemini Live voice engine (nodo/voice/live.py) driven through the real /voice/stream WebSocket:
a fake Live session replaces Google's, so the whole bridge is tested without a key or network."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from nodo.db.models import Approval

WS = "/api/v1/voice/stream"


def _sc(**kw):
    kw.setdefault("model_turn", None)
    kw.setdefault("input_transcription", None)
    kw.setdefault("output_transcription", None)
    kw.setdefault("interrupted", False)
    kw.setdefault("turn_complete", False)
    return SimpleNamespace(server_content=SimpleNamespace(**kw), tool_call=None, go_away=None)


def _tc(name, args=None, fid="call-1"):
    return SimpleNamespace(server_content=None, go_away=None,
                           tool_call=SimpleNamespace(
                               function_calls=[SimpleNamespace(id=fid, name=name, args=args or {})]))


class FakeLiveSession:
    """Stands in for a Gemini Live session: scripted incoming messages, recorded outgoing calls."""

    def __init__(self, script: list):
        self.script = list(script)
        self.audio: list[dict] = []
        self.contents: list = []
        self.tool_responses: list = []

    async def send_realtime_input(self, *, audio=None, **kw):
        self.audio.append(audio)

    async def send_client_content(self, *, turns=None, turn_complete=True):
        self.contents.append({"turns": turns, "turn_complete": turn_complete})

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)

    async def receive(self):
        while self.script:
            m = self.script.pop(0)
            await asyncio.sleep(0.01)
            yield m
            # Match google-genai: receive() ends after ONE model turn.
            if getattr(getattr(m, "server_content", None), "turn_complete", False):
                return
        await asyncio.Event().wait()   # a real session stays open; close() cancels this


class _CM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *a):
        return False


def make_connect(script, holder, fail_models=()):
    """A connect factory: fails for some model names (the ladder), records the fake session."""

    def connect(api_key, model, config):
        if model in fail_models:
            raise RuntimeError(f"404 model {model} not found")
        s = FakeLiveSession(script)
        holder.append((model, config, s))
        return _CM(s)
    return connect


def _open_live(ws):
    """session.start only marks the engine as wanted; the live session opens on the first input."""
    ws.send_json({"type": "session.start", "language": "it"})
    _, started = _recv_until(ws, "voice.session.started")
    ws.send_json({"type": "audio.chunk", "data": "AAECAwQ=", "mime": "audio/pcm;rate=16000"})
    return started


def _recv_until(ws, types, limit=300):
    want = (types,) if isinstance(types, str) else types
    events = []
    for _ in range(limit):
        e = ws.receive_json()
        events.append(e)
        if e["type"] in want:
            return events, e
    raise AssertionError(f"never received {want}; got {[e['type'] for e in events]}")


def test_live_turn_streams_audio_and_text(client, container):
    """Mic audio goes to Gemini; its speech and transcription come back as protocol events."""
    script = [_sc(input_transcription=SimpleNamespace(text="come stanno i siti")),
              _sc(output_transcription=SimpleNamespace(text="Tutto regolare. ")),
              _sc(model_turn=SimpleNamespace(parts=[SimpleNamespace(
                  inline_data=SimpleNamespace(data=b"\x00\x01" * 200, mime_type="audio/pcm"), text=None)])),
              _sc(output_transcription=SimpleNamespace(text="Tre siti ok.")),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        started = _open_live(ws)
        assert started["engine"] == "live"
        events, done = _recv_until(ws, "voice.turn.done")
        types = [e["type"] for e in events]
        assert "transcript.final" in types and "nodo.token" in types and "tts.chunk" in types
        resp = next(e for e in events if e["type"] == "nodo.response")
        assert resp["text"] == "Tutto regolare. Tre siti ok." and resp["provider"] == "gemini-live"
        chunk = next(e for e in events if e["type"] == "tts.chunk")
        assert chunk["mime"] == "audio/pcm;rate=24000"
    assert holder and holder[0][2].audio            # the mic PCM reached the engine
    assert holder[0][0].startswith("gemini")        # a real model name from the ladder


def test_live_tool_call_uses_the_same_toolbox(client, container, seeded):
    """A function call from Gemini runs NODO's tools (with their guards) and answers the model."""
    script = [_sc(input_transcription=SimpleNamespace(text="fammi il punto dei siti")),
              _tc("sites_status"),
              _sc(output_transcription=SimpleNamespace(text="I siti sono a posto.")),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        started = _open_live(ws)
        assert started["engine"] == "live"
        events, _ = _recv_until(ws, "voice.turn.done")
        steps = [e for e in events if e["type"] == "nodo.step"]
        assert any(s["step"] == "tool:sites_status" and s["status"] == "done" for s in steps)
    session = holder[0][2]
    assert session.tool_responses and session.tool_responses[0]["name"] == "sites_status"
    assert "sites" in session.tool_responses[0]["response"]["result"]


def test_live_barge_in(client, container):
    """Gemini reports the user spoke over it -> we forward a real interrupt and listen again."""
    script = [_sc(output_transcription=SimpleNamespace(text="Sto rispondendo")),
              _sc(interrupted=True),
              _sc(input_transcription=SimpleNamespace(text="basta")),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        started = _open_live(ws)
        assert started["engine"] == "live"
        events, interrupted = _recv_until(ws, "voice.interrupted")
        assert interrupted["reason"] == "barge_in"
        more, _ = _recv_until(ws, "voice.turn.done")
        assert any(e["type"] == "voice.session.state" and e["state"] == "LISTENING"
                   for e in events + more)


def test_live_ladder_tries_the_next_model(client, container):
    """A model name this key does not have -> the next rung on the ladder is tried."""
    from nodo.voice.live import LIVE_MODELS
    holder = []
    container.live_connect = make_connect([_sc(turn_complete=True)], holder,
                                          fail_models={LIVE_MODELS[0]})
    with client.websocket_connect(WS) as ws:
        started = _open_live(ws)
        assert started["engine"] == "live"
        _recv_until(ws, "voice.turn.done")
    assert holder and holder[0][0] == LIVE_MODELS[1]


def test_live_reconnects_once_when_the_session_drops(client, container):
    """A dropped live session reconnects transparently once (no death loop)."""
    class Dying(FakeLiveSession):
        async def receive(self):
            yield _sc(turn_complete=True)
            raise RuntimeError("websocket closed")          # the session dies

    holder = []

    def connect(api_key, model, config):
        # first session dies after one message; the transparent reconnect must open a second one
        s = Dying([_sc(turn_complete=True)]) if not holder else FakeLiveSession([])
        holder.append((model, s))
        return _CM(s)

    container.live_connect = connect
    with client.websocket_connect(WS) as ws:
        started = _open_live(ws)
        assert started["engine"] == "live"
        _recv_until(ws, "voice.turn.done")
        deadline = time.time() + 5
        while len(holder) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert len(holder) == 2          # reconnected once, still live — no error, no loop


def test_live_disabled_without_engine(client, container):
    """No live engine configured (tests run in TEST env): the browser chain answers as always."""
    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "session.start", "language": "it", "client_stt": True})
        _, started = _recv_until(ws, "voice.session.started")
        assert started.get("engine") in (None, "browser")


def test_live_config_endpoint_reports_engine(client, container):
    container.live_connect = make_connect([], [])
    r = client.get("/api/v1/voice/config")
    assert r.json()["engine"] == "live"


def test_save_key_from_the_panel(client, container, tmp_path, monkeypatch):
    """Pasting a key in Accessi writes it to .env and activates it without a restart."""
    env = tmp_path / ".env"
    env.write_text("NODO_STT_PROVIDER=browser\n# NODO_GEMINI_API_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("NODO_ENV_FILE", str(env))
    r = client.post("/api/v1/setup/key", json={"name": "nodo_gemini_api_key", "value": " AIza-test "})
    assert r.status_code == 200 and r.json()["ok"]
    text = env.read_text(encoding="utf-8")
    assert "NODO_GEMINI_API_KEY=AIza-test" in text and "NODO_STT_PROVIDER=browser" in text
    assert container.settings.gemini_api_key == "AIza-test"        # live, no restart needed
    assert any(p.name == "gemini" for p in container.providers)    # provider list rebuilt too
    r = client.post("/api/v1/setup/key", json={"name": "NODO_DATABASE_URL", "value": "x"})
    assert r.status_code == 400                                    # only whitelisted keys
    r = client.post("/api/v1/setup/key", json={"name": "NODO_GEMINI_API_KEY", "value": "  "})
    assert r.status_code == 400


def test_approval_answer_speaks_through_live(client, db, container):
    """Clicking 'approve' in the popup while live: the ack is spoken by Gemini, not browser TTS."""
    from nodo.app import ensure_workspace
    from nodo.voice.base import VoiceState
    org, _ = ensure_workspace(db)
    a = Approval(organization_id=org.id, request_id="test", tool_name="github", action="create_issue",
                 level=3, payload={"title": "x"}, status="pending")
    db.add(a)
    db.commit()
    holder = []
    container.live_connect = make_connect([], holder)
    with client.websocket_connect(WS) as ws:
        started = _open_live(ws)
        vs = container.voice_sessions.get(started["session_id"])
        vs.pending_approval_id = a.id
        vs.state = VoiceState.WAITING_FOR_APPROVAL
        ws.send_json({"type": "approval.answer", "approval_id": a.id, "decision": "approve"})
        _, resolved = _recv_until(ws, "approval.resolved")
        assert resolved["status"] in ("approved", "ok", "done")
        time.sleep(0.5)   # let the server push the spoken ack into the live session before closing
    session = holder[0][2]
    assert session.contents and session.contents[-1]["turn_complete"] is True


def test_live_two_turns_keep_one_connection_and_persist_diary(client, container):
    """Regression: SDK's per-turn iterator must not be mistaken for a disconnect."""
    holder, script = [], []
    for question, answer in (("primo messaggio", "Prima risposta."), ("secondo messaggio", "Seconda risposta.")):
        script += [_sc(input_transcription=SimpleNamespace(text=question)),
                   _sc(output_transcription=SimpleNamespace(text=answer)), _sc(turn_complete=True)]
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.turn.done")
        events2, _ = _recv_until(ws, "voice.turn.done")
        assert len(holder) == 1
        assert not any(e["type"] == "voice.engine" for e in events + events2)
        response = next(e for e in events2 if e["type"] == "nodo.response")
        conversation = response["conversation_id"]
        first_types = [e["type"] for e in events]
        assert first_types.index("transcript.final") < first_types.index("nodo.token")
    messages = client.get(f"/api/v1/conversations/{conversation}/messages").json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "primo messaggio"), ("nodo", "Prima risposta."),
        ("user", "secondo messaggio"), ("nodo", "Seconda risposta.")]


def test_live_recovery_reuses_handle_and_model(client, container):
    class ResumeThenDrop(FakeLiveSession):
        async def receive(self):
            yield SimpleNamespace(session_resumption_update=SimpleNamespace(resumable=True, new_handle="resume-test"))
            raise RuntimeError("1011 temporary network failure")
    holder = []
    def connect(key, model, config):
        session = ResumeThenDrop([]) if not holder else FakeLiveSession([])
        holder.append((model, config, session))
        return _CM(session)
    container.live_connect = connect
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        _recv_until(ws, "voice.interrupted")
        while True:
            _, event = _recv_until(ws, "voice.connection")
            if event["status"] == "connected":
                break
        assert len(holder) == 2
        assert holder[1][0] == holder[0][0]
        assert holder[1][1]["session_resumption"] == {"handle": "resume-test"}


def test_live_quota_degrades_without_cycling_models(client, container):
    class Quota(FakeLiveSession):
        async def receive(self):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
            yield  # async generator
    holder = []
    def connect(key, model, config):
        holder.append(model)
        return _CM(Quota([]))
    container.live_connect = connect
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        _, event = _recv_until(ws, "voice.engine")
        assert event["engine"] == "browser"
    assert len(holder) == 1


async def test_live_stop_survives_incoming_mic_frames(container, engine):
    from nodo.db.session import session_scope
    from nodo.voice.base import VoiceState
    from nodo.voice.protocol import AudioChunk, SessionStart
    from nodo.voice.runtime import VoiceRuntime
    events = []
    async def emit(event):
        events.append(event)
    container.live_connect = make_connect([], [])
    vs = container.voice_sessions.create(None, "it")
    runtime = VoiceRuntime(vs, container, emit, session_scope)
    await runtime.handle(SessionStart())
    await runtime.handle(AudioChunk(data="AAEAAg=="))
    vs.state = VoiceState.SPEAKING
    await runtime.interrupt()
    events.clear()
    await runtime.handle(AudioChunk(data="AAEAAg=="))
    await runtime._on_live_event({"kind": "audio", "data": b"\x00\x00"})
    assert not any(e["type"] == "tts.chunk" for e in events)
    await runtime.close()


async def test_live_goaway_and_resumability_are_distinct(settings):
    from nodo.voice.live import LiveBridge
    events = []
    async def emit(e):
        events.append(e)
    session = FakeLiveSession([
        SimpleNamespace(session_resumption_update=SimpleNamespace(resumable=True, new_handle="first")),
        SimpleNamespace(session_resumption_update=SimpleNamespace(resumable=False, new_handle=None)),
        SimpleNamespace(go_away=SimpleNamespace(time_left="30s")), _sc(turn_complete=True)])
    bridge = LiveBridge(settings, emit, lambda *a: _CM(session))
    await bridge.start("Italiano", [], "it")
    for _ in range(30):
        if any(e["kind"] == "go_away" for e in events):
            break
        await asyncio.sleep(.01)
    assert bridge.resume_handle is None
    assert any(e["kind"] == "go_away" for e in events)
    assert not any(e["kind"] == "closed" for e in events)
    await bridge.close()


def test_live_config_validates_with_real_sdk(settings):
    import pytest
    types = pytest.importorskip("google.genai.types")
    from nodo.voice.live import live_config
    config = types.LiveConnectConfig(**live_config(settings, "Parla italiano", [], "resume-test"))
    assert config.session_resumption.handle == "resume-test"
    assert config.realtime_input_config.automatic_activity_detection.disabled is False
    assert config.speech_config.language_code == "it-IT"       # Italian ears: no Hindi/Korean transcripts of noise
    settings.live_language = ""
    config = types.LiveConnectConfig(**live_config(settings, "Parla italiano", [], None))
    assert config.speech_config.language_code is None


async def test_live_mic_off_flushes_server_vad(settings):
    from nodo.voice.live import LiveBridge
    flags = []
    class AudioEnd(FakeLiveSession):
        async def send_realtime_input(self, **kw):
            flags.append(kw)
    async def emit(e):
        pass
    bridge = LiveBridge(settings, emit, lambda *a: _CM(AudioEnd([])))
    await bridge.start("Italiano", [], "it")
    await bridge.end_audio()
    assert flags == [{"audio_stream_end": True}]
    await bridge.close()


def test_live_turn_is_saved_to_the_shared_diary(client, db, container):
    """Speech-to-speech must persist transcribed user and NODO messages for later text turns."""
    from sqlalchemy import select

    from nodo.db.models import Message

    holder = []
    container.live_connect = make_connect([
        _sc(input_transcription=SimpleNamespace(text="Come va il progetto?")),
        _sc(output_transcription=SimpleNamespace(text="Controllo gli aggiornamenti.")),
        _sc(turn_complete=True),
    ], holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.turn.done")
        response = next(e for e in events if e["type"] == "nodo.response")
        conv_id = response["conversation_id"]
        assert conv_id
    db.expire_all()
    saved = db.scalars(select(Message).where(Message.conversation_id == conv_id)
                       .order_by(Message.created_at.asc())).all()
    assert [(m.role, m.content) for m in saved] == [
        ("user", "Come va il progetto?"),
        ("nodo", "Controllo gli aggiornamenti."),
    ]


def test_text_conversation_can_be_selected_before_speaking(client, db, container):
    """Changing the active text thread synchronizes Live context without a second voice session."""
    from sqlalchemy import select

    from nodo.app import ensure_workspace
    from nodo.db.models import Conversation, Message

    org, _ = ensure_workspace(db)
    conv = Conversation(organization_id=org.id, channel="text")
    db.add(conv)
    db.commit()
    holder = []
    container.live_connect = make_connect([
        _sc(input_transcription=SimpleNamespace(text="E poi?")),
        _sc(output_transcription=SimpleNamespace(text="Continuiamo da qui.")),
        _sc(turn_complete=True),
    ], holder)

    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "session.start", "language": "it"})
        _recv_until(ws, "voice.session.started")
        ws.send_json({"type": "conversation.set", "conversation_id": conv.id})
        _, sync = _recv_until(ws, "voice.conversation")
        assert sync["conversation_id"] == conv.id
        ws.send_json({"type": "audio.chunk", "data": "AAECAwQ=", "mime": "audio/pcm;rate=16000"})
        events, _ = _recv_until(ws, "voice.turn.done")
        response = next(e for e in events if e["type"] == "nodo.response")
        assert response["conversation_id"] == conv.id

    db.expire_all()
    messages = db.scalars(select(Message).where(Message.conversation_id == conv.id)).all()
    assert [m.role for m in messages] == ["user", "nodo"]


def test_conversation_set_is_a_typed_protocol_event():
    from nodo.voice.protocol import ConversationSet, parse_inbound
    parsed = parse_inbound({"type": "conversation.set", "conversation_id": None})
    assert isinstance(parsed, ConversationSet)
    assert parsed.conversation_id is None


def test_quota_error_rests_the_key_instead_of_retrying_the_ladder(container):
    """A 1011 quota refusal marks the key: the ladder stops and missing_reason explains why."""
    import pytest

    from nodo.voice import live as live_engine

    calls = []

    def quota_connect(api_key, model, config):
        calls.append(model)
        raise RuntimeError("1011 None. You exceeded your current quota, please check your plan")

    try:
        live_engine._quota["key"] = None
        live_engine._quota["until"] = 0.0
        container.settings.env = "LOCAL"
        container.settings.voice_engine = "auto"
        container.settings.gemini_api_key = "test-key"
        container.live_connect = None
        bridge = live_engine.LiveBridge(container.settings, lambda e: None, connect=quota_connect)
        with pytest.raises(RuntimeError):
            asyncio.run(bridge.start("sys", [], "it"))
        assert len(calls) == 1                       # Google quota is per project; stop the ladder
        assert live_engine.quota_note(container.settings) is not None
        assert live_engine.missing_reason(container.settings) == live_engine.quota_note(container.settings)
        container.settings.gemini_api_key = "another-key"
        assert live_engine.quota_note(container.settings) is None   # no local cache entry; NOT a fresh Google quota
    finally:
        container.settings.env = "TEST"
        container.settings.gemini_api_key = None
        live_engine._quota["key"] = None
        live_engine._quota["until"] = 0.0


def test_generic_1011_is_not_mistaken_for_exhausted_quota():
    """WebSocket 1011 by itself is an internal server error, not Google's quota response."""
    from nodo.voice import live as live_engine

    assert not live_engine._is_quota_error(RuntimeError("1011 internal error: backend restart"))
    assert live_engine._is_quota_error(RuntimeError("1011 None. You exceeded your current quota"))
    assert live_engine._is_quota_error(RuntimeError("429 RESOURCE_EXHAUSTED"))


def test_quota_guard_keeps_only_key_fingerprint(container):
    """Quota backoff must not create an additional raw API key copy in process globals."""
    from nodo.voice import live as live_engine

    old = dict(live_engine._quota)
    try:
        container.settings.gemini_api_key = "example-secret"
        live_engine._mark_quota(container.settings)
        assert live_engine._quota["key"] != "example-secret"
        assert live_engine.quota_note(container.settings)
        assert "progetto" in live_engine.quota_note(container.settings)
    finally:
        container.settings.gemini_api_key = None
        live_engine._quota.update(old)


def test_live_quota_during_session_skips_reconnect_and_falls_back(client, container):
    """A quota error after Live started must stop retries and return the user to browser STT."""
    class QuotaDying(FakeLiveSession):
        async def receive(self):
            yield _sc(input_transcription=SimpleNamespace(text="Ciao NODO"))
            yield _sc(output_transcription=SimpleNamespace(text="Eccomi."))
            yield _sc(turn_complete=True)
            raise RuntimeError("1011 None. You exceeded your current quota")

    holder = []

    def connect(api_key, model, config):
        holder.append(model)
        return _CM(QuotaDying([]))

    from nodo.voice import live as live_engine

    container.live_connect = connect
    old = dict(live_engine._quota)
    try:
        # A test hook supplies a fake transport regardless of production availability.
        with client.websocket_connect(WS) as ws:
            _open_live(ws)
            events, engine = _recv_until(ws, "voice.engine")
            assert engine["engine"] == "browser"
            assert engine["reason"] == "quota"
            assert len(holder) == 1
            assert any(e["type"] == "voice.turn.done" for e in events)
    finally:
        live_engine._quota.update(old)


async def test_live_approval_cannot_target_another_visible_binding(container, monkeypatch):
    from nodo.brain.tools import Toolbox
    from nodo.db.session import session_scope
    from nodo.voice.runtime import VoiceRuntime

    calls, events = [], []
    async def forbidden_call(*args):
        calls.append(args)
        raise AssertionError("An unbound decision must not reach Toolbox")
    async def emit(event):
        events.append(event)
    monkeypatch.setattr(Toolbox, "call", forbidden_call)
    vs = container.voice_sessions.create(None, "it")
    vs.pending_approval_id = "shown-now"
    runtime = VoiceRuntime(vs, container, emit, session_scope)
    await runtime._live_tool_calls([{"id": "decision-call", "name": "decide", "user_text": "sì",
                                    "args": {"approval_id": "shown-earlier", "approve": True}}])
    assert not calls
    assert vs.pending_approval_id == "shown-now"
    assert any(e.get("ok") is False for e in events)
    await runtime.close()


async def test_live_slow_tool_does_not_block_barge_in(container, engine, monkeypatch):
    from nodo.brain.tools import Toolbox
    from nodo.db.session import session_scope
    from nodo.voice.protocol import AudioChunk, SessionStart
    from nodo.voice.runtime import VoiceRuntime

    started, finish = asyncio.Event(), asyncio.Event()
    events = []
    async def slow_call(self, name, args):
        started.set()
        await finish.wait()
        return {"sites": []}
    async def emit(event):
        events.append(event)
    monkeypatch.setattr(Toolbox, "call", slow_call)
    container.live_connect = make_connect([], [])
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    await runtime.handle(SessionStart())
    await runtime.handle(AudioChunk(data="AAEAAg=="))
    try:
        await asyncio.wait_for(runtime._on_live_event({"kind": "tool_call", "id": "slow",
                                                      "name": "sites_status", "args": {}}), .5)
        await asyncio.wait_for(started.wait(), .5)
        await runtime._on_live_event({"kind": "interrupted"})
        assert any(e["type"] == "voice.interrupted" for e in events)
        finish.set()
        await asyncio.wait_for(asyncio.gather(*runtime._live_tools.values()), .5)
    finally:
        finish.set()
        await runtime.close()


def test_repeated_short_connections_have_a_finite_retry_budget(client, container):
    class Flapping(FakeLiveSession):
        async def receive(self):
            raise RuntimeError("1011 internal error: backend restart")
            yield
    attempts = []
    def connect(key, model, config):
        attempts.append(model)
        return _CM(Flapping([]))
    container.live_connect = connect
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        _, fallback = _recv_until(ws, "voice.engine")
        assert fallback["reason"] == "connection"
        assert len(attempts) == 4  # initial connection plus at most three recoveries
        assert len(set(attempts)) == 1


async def test_stopping_finished_playback_does_not_mute_the_next_reply(container, engine):
    from nodo.db.session import session_scope
    from nodo.voice.base import VoiceState
    from nodo.voice.protocol import AudioChunk, SessionStart
    from nodo.voice.runtime import VoiceRuntime

    events = []
    async def emit(event):
        events.append(event)
    container.live_connect = make_connect([], [])
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    await runtime.handle(SessionStart())
    await runtime.handle(AudioChunk(data="AAEAAg=="))
    runtime.vs.state = VoiceState.LISTENING
    runtime.vs.turn_active = False  # backend completed; the browser may still have queued audio
    await runtime.interrupt(reason="microphone_off")
    await runtime._on_live_event({"kind": "input", "text": "Nuova domanda"})
    await runtime._on_live_event({"kind": "audio", "data": b"\x00\x00"})
    assert any(e["type"] == "tts.chunk" for e in events)
    await runtime.close()


async def test_persisting_a_live_turn_waits_out_a_locked_database():
    """The night shift can hold the SQLite write lock for a moment: the voice retries instead of losing the turn."""
    from sqlalchemy.exc import OperationalError

    from nodo.voice.runtime import _retry_locked

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))

    await _retry_locked(flaky)
    assert calls["n"] == 3

    def broken():
        raise OperationalError("COMMIT", {}, Exception("no such table: messages"))

    with pytest.raises(OperationalError):
        await _retry_locked(broken)


def test_live_pensa_hands_the_answer_to_the_claude_brain(client, container, monkeypatch):
    """Live keeps ears and mouth; «pensa» runs the real brain (memory, register, guard) and returns its words."""
    from nodo.brain import loop
    seen = []

    async def fake_run(self, text):
        assert self.directed
        seen.append(text)
        yield {"type": "step", "request_id": "r", "at": "t", "step": "tool:ledger_report", "status": "done",
               "ok": True}
        self.text = "Marcello ci deve 30 euro, ancora non li ha portati."

    monkeypatch.setattr(loop.Brain, "run", fake_run)
    script = [_sc(input_transcription=SimpleNamespace(text="Quanto mi deve Marcello?")),
              _tc("pensa", {"richiesta": "quanto mi deve Marcello"}),
              _sc(output_transcription=SimpleNamespace(text="Trenta euro, ancora non li ha portati.")),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.turn.done")
    model, config, session = holder[0]
    names = [d["name"] for d in config["tools"][0]["function_declarations"]]
    assert names[0] == "pensa" and "ledger_add" not in names and "decide" in names   # Gemini cannot act alone
    assert "LAVAGNA VIVA" in config["system_instruction"] and "pensa" in config["system_instruction"]
    assert seen == ["Quanto mi deve Marcello?"]
    result = session.tool_responses[0]["response"]["result"]
    assert result["risposta"].startswith("Marcello ci deve 30 euro")
    assert any(e["type"] == "nodo.step" and e["step"] == "tool:ledger_report" for e in events)


async def test_pensa_silence_and_brain_down(container, engine, monkeypatch):
    from nodo.brain import loop
    from nodo.db.session import session_scope
    from nodo.voice.runtime import VoiceRuntime

    async def quiet(self, text):
        self.silent, self.text = True, ""
        return
        yield

    async def emit(event):
        pass
    monkeypatch.setattr(loop.Brain, "run", quiet)
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    runtime._in_text = "Mi dica signora, lo viene a ritirare lei?"
    quiet_result = await runtime._live_think({}, [])
    assert quiet_result["risposta"] == "" and "silenzio" not in str(quiet_result)   # never a word to read aloud
    assert runtime._live_muted and runtime._pensa_error                           # and no second pensa this turn
    runtime._live_muted, runtime._pensa_error = False, None

    async def down(self, text):
        raise loop.BrainUnavailable("nessun modello")
        yield
    monkeypatch.setattr(loop.Brain, "run", down)
    assert "error" in await runtime._live_think({"richiesta": "quanto ho incassato?"}, [])
    await runtime.close()


async def test_pensa_ignores_noise_and_keeps_the_real_question(container, engine, monkeypatch):
    """The shop around him: Spanish from a customer, the TV. Noise alone never wakes the brain (15-30 s each);
    noise plus a clear request from Gemini sends the REQUEST, not the noise."""
    from nodo.brain import loop
    from nodo.db.session import session_scope
    from nodo.voice.runtime import VoiceRuntime
    seen = []

    async def fake_run(self, text):
        assert self.directed
        seen.append(text)
        self.text = "Hai tre sospesi."
        return
        yield

    async def emit(event):
        pass
    monkeypatch.setattr(loop.Brain, "run", fake_run)
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    runtime._in_text = "de la"
    result = await runtime._live_think({}, [])
    assert seen == [] and result["risposta"] == ""
    runtime._live_muted, runtime._pensa_error = False, None
    runtime._in_text = "y vale"
    result = await runtime._live_think({"richiesta": "quali sono i pagamenti sospesi"}, [])
    assert seen[0].startswith("quali sono i pagamenti sospesi") and "vale" not in seen[0]
    assert result["risposta"] == "Hai tre sospesi."
    # the shop around a register question: Gemini's clean version goes to the brain (instant register read)
    runtime._in_text = "allora mi senti quali sono i sospesi"
    await runtime._live_think({"richiesta": "quali sono i sospesi"}, [])
    assert seen[-1] == "quali sono i sospesi"
    await runtime.close()


def test_live_reading_silenzio_aloud_is_cut(client, container):
    """If Gemini starts saying «[silenzio]» the audio stops at once and nothing is kept as her answer."""
    script = [_sc(input_transcription=SimpleNamespace(text="Mi dica, signora")),
              _sc(output_transcription=SimpleNamespace(text="[silenzio")),
              _sc(output_transcription=SimpleNamespace(text="]")),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.turn.done")
    assert any(e["type"] == "voice.interrupted" and e.get("reason") == "quiet" for e in events)
    assert not any(e["type"] == "nodo.response" for e in events)


def test_greet_makes_maria_say_hello_in_her_live_voice(client, container):
    script = [_sc(output_transcription=SimpleNamespace(text="Ciao Gaetano, eccomi.")), _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "session.start", "language": "it"})
        _recv_until(ws, "voice.session.started")
        ws.send_json({"type": "greet"})
        _recv_until(ws, "voice.turn.done")
    session = holder[0][2]
    said = session.contents[0]["turns"][0]["parts"][0]["text"]
    assert "Ciao Gaetano, eccomi. In cosa posso esserti utile oggi?" in said and session.contents[0]["turn_complete"]


def test_ferma_during_pensa_tells_gemini_so_maria_is_not_left_deaf(client, container, monkeypatch):
    """«Ferma» while the brain works: the brain stops AND Gemini gets an answer for its waiting (BLOCKING) call;
    without it the voice waited forever and Maria stopped answering."""
    import time

    from nodo.brain import loop

    async def slow(self, text):
        await asyncio.sleep(30)
        yield {}

    monkeypatch.setattr(loop.Brain, "run", slow)
    script = [_sc(input_transcription=SimpleNamespace(text="quanto ho incassato questa settimana")),
              _tc("pensa", {"richiesta": "quanto ho incassato questa settimana"}, fid="call-9")]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        for _ in range(300):
            e = ws.receive_json()
            if e["type"] == "nodo.step" and e.get("step") == "tool:pensa" and e.get("status") == "running":
                break
        ws.send_json({"type": "interrupt"})
        _recv_until(ws, "voice.interrupted")
        session = holder[0][2]
        for _ in range(100):
            if session.tool_responses:
                break
            time.sleep(0.05)
    assert session.tool_responses, "Gemini never got an answer for the stopped call"
    r = session.tool_responses[0]
    assert r["id"] == "call-9" and "fermata" in r["response"]["result"]["nota"]


async def test_lavagna_viva_has_the_shop_ready(container, engine):
    """The page the live voice reads: today, who has to pay, totals, prices, customers — no brain needed."""
    from datetime import datetime

    from nodo.brain.board import Board
    from nodo.db.session import session_scope
    from nodo.ledger import Ledger
    from nodo.night.briefing import tzinfo
    now = datetime.now(tzinfo(container.settings.timezone))
    with session_scope() as db:
        org = container.core(db).org
        led = Ledger(db, org, "CF Materassi", today=now.date())
        led.add(kind="vendita", item="Arianna H20", size="80x190", qty=2, unit_price=55, counterpart="Softcomfort",
                payment="sospeso")
        led.add(kind="vendita", item="Morpheus Memory", size="160x190", qty=1, unit_price=420, counterpart="Rossi")
        db.flush()
        b = Board()
        assert b.refresh(db, org, container.settings, now)
        text = b.text
        assert "LAVAGNA VIVA" in text and "SOSPESI (1" in text and "Softcomfort" in text and "110" in text
        assert "Morpheus Memory" in text and "420" in text and "OGGI:" in text
        assert not b.refresh(db, org, container.settings, now)      # nothing changed: nothing to send


def test_a_slow_first_handshake_is_retried_not_given_up(client, container, monkeypatch):
    """On the shop laptop one Live handshake timed out and the voice fell back for good to a chain that could not
    hear him. Now a second try comes first."""
    monkeypatch.setattr(container.settings, "live_connect_timeout", 0.5)
    script = [_sc(input_transcription=SimpleNamespace(text="ciao")), _sc(turn_complete=True)]
    calls = []

    class Hang:
        async def __aenter__(self):
            await asyncio.sleep(5)

        async def __aexit__(self, *a):
            return False

    def connect(api_key, model, config):
        calls.append(model)
        return Hang() if len(calls) == 1 else _CM(FakeLiveSession(script))
    container.live_connect = connect
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.connection")
    assert len(calls) >= 2
    assert not any(e["type"] == "voice.engine" for e in events)


def test_a_spoken_pensa_call_is_made_for_real_and_never_said(client, container, monkeypatch):
    """Gemini sometimes SAYS «pensa(richiesta=…)» instead of calling it (Maria read it aloud, and the request was
    lost). Now the audio is cut, the call is made, and the answer goes back to the voice to be said."""
    import time

    from nodo.brain import loop
    seen = []

    async def fake_run(self, text):
        seen.append(text)
        self.text = "Fatto: ho creato l'immagine del gatto."
        return
        yield

    monkeypatch.setattr(loop.Brain, "run", fake_run)
    script = [_sc(input_transcription=SimpleNamespace(text="fammi un'immagine di un gatto")),
              _sc(output_transcription=SimpleNamespace(text='pensa(richiesta="crea un')),
              _sc(output_transcription=SimpleNamespace(text=' immagine di un gatto")')),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.turn.done")
        session = holder[0][2]
        for _ in range(100):
            if any("Risultato di pensa" in str(c) for c in session.contents):
                break
            time.sleep(0.05)
    assert seen and "gatto" in seen[0]
    assert any("Risultato di pensa" in str(c) and "creato l'immagine" in str(c) for c in session.contents)
    assert not any(e["type"] == "nodo.response" and "pensa(" in e.get("text", "") for e in events)


def test_a_refused_show_request_is_done_by_the_brain(client, container, monkeypatch):
    """«Mostrami l'ultima foto» → «purtroppo non posso mostrare finestre»: the refusal is cut, the brain shows it."""
    import time

    from nodo.brain import loop
    seen = []

    async def fake_run(self, text):
        seen.append(text)
        yield {"type": "ui.present", "request_id": "r", "at": "t", "view": "viewer",
               "rows": [{"title": "foto", "url": "/api/v1/view/x/foto.jpg", "kind": "image"}], "summary": ""}
        self.text = "Ecco la foto sullo schermo."

    monkeypatch.setattr(loop.Brain, "run", fake_run)
    script = [_sc(input_transcription=SimpleNamespace(text="mostrami l'ultima foto del desktop")),
              _sc(output_transcription=SimpleNamespace(text="Purtroppo non ho la possibilità di mostrare finestre.")),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.turn.done")
        more, _ = _recv_until(ws, "ui.present")
        session = holder[0][2]
        for _ in range(100):
            if any("Risultato di pensa" in str(c) for c in session.contents):
                break
            time.sleep(0.05)
    assert seen and "ultima foto" in seen[0]
    assert any(e["type"] == "voice.interrupted" and e.get("reason") == "quiet" for e in events)
    assert not any(e["type"] == "nodo.response" and "Purtroppo" in e.get("text", "") for e in events)
    assert any("Risultato di pensa" in str(c) for c in session.contents)


async def test_drawing_an_image_gets_its_time(container, engine, monkeypatch):
    """The 40-second rule is for thinking: an image that takes longer is not cut as «ci sto mettendo troppo»."""
    from nodo.brain import loop
    from nodo.db.session import session_scope
    from nodo.voice.runtime import VoiceRuntime

    async def slow_image(self, text):
        yield {"type": "step", "request_id": "r", "at": "t", "step": "tool:generate_image", "status": "running",
               "arguments": {"description": "un tubo"}}
        await asyncio.sleep(0.8)                       # longer than the (shrunk) thinking limit below
        yield {"type": "step", "request_id": "r", "at": "t", "step": "tool:generate_image", "status": "done"}
        self.text = "Ecco il tubo."

    async def plain_slow(self, text):
        await asyncio.sleep(0.8)
        self.text = "troppo tardi"
        return
        yield

    async def emit(event):
        pass
    monkeypatch.setattr(container.settings, "brain_deadline_s", -24.6)   # thinking limit = 0.4 s
    runtime = VoiceRuntime(container.voice_sessions.create(None, "it"), container, emit, session_scope)
    monkeypatch.setattr(loop.Brain, "run", slow_image)
    assert (await runtime._live_think({"richiesta": "fammi un'immagine di un tubo"}, [])).get("risposta") == "Ecco il tubo."
    monkeypatch.setattr(loop.Brain, "run", plain_slow)
    assert "error" in await runtime._live_think({"richiesta": "quanto ho fatto oggi"}, [])
    await runtime.close()


def test_maria_sings_over_a_band(client, container):
    """«Cantami una canzone»: the voice calls «canta», the panel starts the band, she sings, the band fades out."""
    script = [_sc(input_transcription=SimpleNamespace(text="Maria cantami una canzone sul negozio")),
              _tc("canta", {"stile": "pop"}, fid="song-1"),
              _sc(output_transcription=SimpleNamespace(text="Materassi d'oro, sogni di velluto…")),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.turn.done")
    model, config, session = holder[0]
    names = [d["name"] for d in config["tools"][0]["function_declarations"]]
    assert "canta" in names
    start = next(e for e in events if e["type"] == "music.start")
    assert start["style"] == "pop"
    assert any(e["type"] == "music.stop" for e in events)
    assert session.tool_responses[0]["response"]["result"]["ok"] is True


def test_the_voice_reads_the_register_itself_and_the_hologram_appears(client, container):
    """«Cosa ho venduto il 12?»: no brain round — the voice calls the register directly, the hologram shows."""
    script = [_sc(input_transcription=SimpleNamespace(text="cosa ho venduto il 12 ottobre")),
              _tc("ledger_report", {"day": "12/10"}, fid="led-1"),
              _sc(output_transcription=SimpleNamespace(text="Il 12 hai venduto…")),
              _sc(turn_complete=True)]
    holder = []
    container.live_connect = make_connect(script, holder)
    with client.websocket_connect(WS) as ws:
        _open_live(ws)
        events, _ = _recv_until(ws, "voice.turn.done")
    names = [d["name"] for d in holder[0][1]["tools"][0]["function_declarations"]]
    assert {"ledger_report", "ledger_open", "ledger_customer"} <= set(names) and "ledger_add" not in names
    assert any(e["type"] == "ui.present" and e["view"] == "ledger" for e in events)
    assert "say" in holder[0][2].tool_responses[0]["response"]["result"]
