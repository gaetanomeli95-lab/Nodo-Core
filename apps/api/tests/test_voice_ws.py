"""Realtime voice over WebSocket (Phase 2): lifecycle, turns, interrupt, approvals, latency, fallbacks."""
from __future__ import annotations

from nodo.db.models import Approval
from nodo.voice.providers import FakeStreamingSTT, FakeStreamingTTS

WS = "/api/v1/voice/stream"


def _recv_until(ws, types: str | tuple[str, ...], limit: int = 200) -> tuple[list[dict], dict]:
    """Collect events until one of `types` arrives; returns (all events, the matching one)."""
    want = (types,) if isinstance(types, str) else types
    events = []
    for _ in range(limit):
        e = ws.receive_json()
        events.append(e)
        if e["type"] in want:
            return events, e
    raise AssertionError(f"never received {want}; got {[e['type'] for e in events]}")


def _start(ws):
    ws.send_json({"type": "session.start", "language": "it", "client_stt": True})
    events, started = _recv_until(ws, "voice.session.started")
    return started


def test_ws_session_lifecycle_and_turn(client):
    with client.websocket_connect(WS) as ws:
        started = _start(ws)
        assert started["session_id"] and started["state"] == "IDLE"
        ws.send_json({"type": "transcript.partial", "text": "fammi il punto"})
        ws.send_json({"type": "transcript.final", "text": "fammi il punto su prosperya"})
        events, done = _recv_until(ws, "voice.turn.done")
        types = [e["type"] for e in events]
        assert "transcript.partial" in types and "nodo.intent" in types and "nodo.plan" in types
        assert "nodo.token" in types and "nodo.sentence" in types and "nodo.response" in types
        assert "voice.session.state" in types
        resp = next(e for e in events if e["type"] == "nodo.response")
        assert "Prosperya" in resp["text"] and resp["intent"] == "project_status"
        intent_ev = next(e for e in events if e["type"] == "nodo.intent")
        assert intent_ev["name"] == "project_status"
        assert "total" in done["latency_ms"] and "model_ttft" in done["latency_ms"]
        # turn ends idle
        assert events[-1]["type"] == "voice.turn.done"


def test_ws_context_switch_and_compare(client):
    with client.websocket_connect(WS) as ws:
        _start(ws)
        ws.send_json({"type": "transcript.final", "text": "fammi il punto su prosperya"})
        _, done1 = _recv_until(ws, "voice.turn.done")
        ws.send_json({"type": "transcript.final", "text": "e PB CARe?"})
        events2, _ = _recv_until(ws, "voice.turn.done")
        resp2 = next(e for e in events2 if e["type"] == "nodo.response")
        assert "PB CARe" in resp2["text"]
        intent2 = next(e for e in events2 if e["type"] == "nodo.intent")
        assert intent2["reference_reapplied"] or intent2["from_context"]
        ws.send_json({"type": "transcript.final", "text": "quale delle due è più urgente?"})
        events3, _ = _recv_until(ws, "voice.turn.done")
        resp3 = next(e for e in events3 if e["type"] == "nodo.response")
        assert next(e for e in events3 if e["type"] == "nodo.intent")["name"] == "compare"
        assert "PB CARe" in resp3["text"] and "Prosperya" in resp3["text"]


def test_ws_interrupt_during_turn(client):
    with client.websocket_connect(WS) as ws:
        _start(ws)
        ws.send_json({"type": "transcript.final", "text": "fammi il punto su prosperya"})
        # wait until generation is underway (or already done), then interrupt — always legal
        _recv_until(ws, ("nodo.token", "voice.turn.done"))
        ws.send_json({"type": "interrupt", "reason": "barge_in"})
        events, interrupted = _recv_until(ws, "voice.interrupted")
        assert interrupted["reason"] == "barge_in"
        # read a few more events: the post-interrupt state machine lands on LISTENING
        for _ in range(4):
            e = ws.receive_json()
            events.append(e)
            if e["type"] == "voice.session.state" and e["state"] == "LISTENING":
                break
        states = [e["state"] for e in events if e["type"] == "voice.session.state"]
        assert "LISTENING" in states  # NODO stops and listens
        # session is still usable afterwards
        ws.send_json({"type": "transcript.final", "text": "cosa devo fare oggi"})
        events2, _ = _recv_until(ws, "voice.turn.done")
        assert next(e for e in events2 if e["type"] == "nodo.intent")["name"] == "today"


def test_ws_stop_command_is_fast_path(client):
    with client.websocket_connect(WS) as ws:
        _start(ws)
        ws.send_json({"type": "transcript.final", "text": "fermati"})
        events, interrupted = _recv_until(ws, "voice.interrupted")
        assert interrupted["reason"] == "stop_command"
        assert "nodo.intent" not in [e["type"] for e in events]  # no reasoning pipeline was entered


def test_ws_approval_binding(client, db, container):
    from nodo.app import ensure_workspace
    org, _ = ensure_workspace(db)
    a = Approval(organization_id=org.id, request_id="test", tool_name="github", action="create_issue", level=3,
                 payload={"title": "x"}, status="pending")
    db.add(a)
    db.commit()  # the runtime resolves through its own session: the row must be committed
    with client.websocket_connect(WS) as ws:
        started = _start(ws)
        sid = started["session_id"]
        vs = container.voice_sessions.get(sid)
        # unbound explicit answer is ignored
        ws.send_json({"type": "approval.answer", "approval_id": a.id, "decision": "approve"})
        _, ignored = _recv_until(ws, "approval.ignored")
        assert ignored["reason"] == "approval not bound to this session"
        # bind the session to the pending approval (normally set by approval.required during a turn)
        vs.pending_approval_id = a.id
        from nodo.voice.base import VoiceState
        vs.state = VoiceState.WAITING_FOR_APPROVAL
        ws.send_json({"type": "transcript.final", "text": "sì"})
        _, resolved = _recv_until(ws, "approval.resolved")
        assert resolved["approval_id"] == a.id and resolved["status"] == "approved"
    db.refresh(a)
    assert a.status == "approved"


def test_ws_bare_yes_without_pending_is_not_authorization(client):
    with client.websocket_connect(WS) as ws:
        _start(ws)
        ws.send_json({"type": "transcript.final", "text": "sì"})
        events, _ = _recv_until(ws, "voice.turn.done")
        assert any(e["type"] == "approval.ignored" for e in events)
        assert not any(e["type"] == "approval.resolved" for e in events)


def test_ws_streaming_stt_partials(client, container):
    container.stt = FakeStreamingSTT(text="fammi il punto su prosperya")
    with client.websocket_connect(WS) as ws:
        _start(ws)
        for _ in range(3):
            ws.send_json({"type": "audio.chunk", "data": "AAECAwQ=", "mime": "audio/webm"})
        events = []
        for _ in range(12):  # collect until enough partials arrived
            e = ws.receive_json()
            events.append(e)
            if sum(1 for x in events if x["type"] == "transcript.partial") >= 2:
                break
        partials = [e["text"] for e in events if e["type"] == "transcript.partial"]
        assert len(partials) >= 2 and partials[0] != partials[-1]  # progressive reveals
        ws.send_json({"type": "speech.end"})
        _, done = _recv_until(ws, "voice.turn.done", limit=300)
        assert done["turn"] >= 1


def test_ws_tts_chunks_for_server_side_tts(client, container):
    container.tts = FakeStreamingTTS()
    with client.websocket_connect(WS) as ws:
        _start(ws)
        ws.send_json({"type": "transcript.final", "text": "cosa devo fare oggi"})
        events, _ = _recv_until(ws, "voice.turn.done")
        types = [e["type"] for e in events]
        assert "tts.started" in types and "tts.chunk" in types
        chunks = [e for e in events if e["type"] == "tts.chunk"]
        assert all(c["mime"] == "audio/wav" for c in chunks) and chunks[-1]["final"] is True


def test_ws_invalid_message_yields_typed_error(client):
    with client.websocket_connect(WS) as ws:
        _start(ws)
        ws.send_text('{"type": "nope"}')
        _, err = _recv_until(ws, "error")
        assert err["where"] == "protocol"


def test_ws_session_end_closes(client):
    with client.websocket_connect(WS) as ws:
        started = _start(ws)
        ws.send_json({"type": "session.end"})
        _, closed = _recv_until(ws, "voice.session.closed")
        assert closed["session_id"] == started["session_id"]
        from nodo.voice.base import VoiceState
        assert client.app.state.container.voice_sessions.get(started["session_id"]).state == VoiceState.CLOSED


def test_ws_turn_metrics_persisted(client, db):
    from sqlalchemy import select

    from nodo.db.models import VoiceTurnMetric
    with client.websocket_connect(WS) as ws:
        _start(ws)
        ws.send_json({"type": "transcript.final", "text": "cosa devo fare oggi"})
        _, done = _recv_until(ws, "voice.turn.done")
    row = db.scalars(select(VoiceTurnMetric).where(VoiceTurnMetric.turn_number == done["turn"])).first()
    assert row is not None and row.turn_done_at and "total" in row.latency_ms
