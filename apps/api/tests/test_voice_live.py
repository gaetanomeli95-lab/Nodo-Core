"""Gemini Live voice engine (nodo/voice/live.py) driven through the real /voice/stream WebSocket:
a fake Live session replaces Google's, so the whole bridge is tested without a key or network."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

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
        for m in self.script:
            await asyncio.sleep(0.01)
            yield m
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
        ws.send_json({"type": "session.start", "language": "it"})
        _, started = _recv_until(ws, "voice.session.started")
        assert started["engine"] == "live"
        ws.send_json({"type": "audio.chunk", "data": "AAECAwQ=", "mime": "audio/pcm;rate=16000"})
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
        ws.send_json({"type": "session.start", "language": "it"})
        _recv_until(ws, "voice.session.started")
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
        ws.send_json({"type": "session.start", "language": "it"})
        _recv_until(ws, "voice.session.started")
        events, interrupted = _recv_until(ws, "voice.interrupted")
        assert interrupted["reason"] == "barge_in"
        more, _ = _recv_until(ws, "voice.turn.done")
        assert any(e["type"] == "voice.session.state" and e["state"] == "LISTENING"
                   for e in events + more)


def test_live_ladder_tries_the_next_model(client, container):
    """A model name this key does not have -> the next rung on the ladder is tried."""
    holder = []
    container.live_connect = make_connect([_sc(turn_complete=True)], holder,
                                          fail_models={"gemini-2.5-flash-native-audio-preview-12-2025"})
    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "session.start", "language": "it"})
        _, started = _recv_until(ws, "voice.session.started")
        assert started["engine"] == "live"
    assert holder and holder[0][0] != "gemini-2.5-flash-native-audio-preview-12-2025"


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
        ws.send_json({"type": "session.start", "language": "it"})
        _, started = _recv_until(ws, "voice.session.started")
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
        ws.send_json({"type": "session.start", "language": "it"})
        _, started = _recv_until(ws, "voice.session.started")
        vs = container.voice_sessions.get(started["session_id"])
        vs.pending_approval_id = a.id
        vs.state = VoiceState.WAITING_FOR_APPROVAL
        ws.send_json({"type": "approval.answer", "approval_id": a.id, "decision": "approve"})
        _, resolved = _recv_until(ws, "approval.resolved")
        assert resolved["status"] in ("approved", "ok", "done")
        time.sleep(0.5)   # let the server push the spoken ack into the live session before closing
    session = holder[0][2]
    assert session.contents and session.contents[-1]["turn_complete"] is True
