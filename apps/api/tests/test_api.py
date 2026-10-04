"""API tests: REST entities, SSE command stream, voice endpoints, telemetry, auth."""
from __future__ import annotations

import json

import pytest

from nodo.voice.base import InvalidTransition, VoiceSession, VoiceState


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]


def test_health_and_system(client):
    assert client.get("/health").json()["mode"] == "FREE"
    sysinfo = client.get("/api/v1/system").json()
    assert sysinfo["mode"] == "FREE" and any(m["provider"] == "deterministic" for m in sysinfo["models"])
    assert sysinfo["voice"]["stt"]["name"] == "fake"


def test_create_project_then_ask_nodo(client):
    c = client.post("/api/v1/clients", json={"name": "Arredo Chef", "sector": "ristorazione"}).json()
    p = client.post("/api/v1/projects", json={"name": "Arredo Chef Sito", "client_id": c["id"], "kind": "software"})
    assert p.status_code == 201
    client.post("/api/v1/tasks", json={"title": "Wireframe home", "project_id": p.json()["id"], "priority": 1})
    with client.stream("POST", "/api/v1/command", json={"text": "Fammi il punto su Arredo Chef Sito"}) as r:
        events = parse_sse(r.read().decode())
    types = [e["type"] for e in events]
    assert types[0] == "intent" and "plan" in types and "context" in types and "agent.started" in types
    assert types.count("token") >= 1 and types[-1] == "done"
    done = events[-1]
    assert "Wireframe home" in done["text"] and done["provider"] == "deterministic" and done["agents"] == ["pm"]
    trace = client.get(f"/api/v1/telemetry/trace/{done['request_id']}").json()
    assert len(trace["inference"]) == 1 and len(trace["agents"]) == 1
    assert client.get(f"/api/v1/graph/client/{c['id']}").json()[0]["kind"] == "has_project"


def test_command_sync_and_conversation_context(client):
    r = client.post("/api/v1/command/sync", json={"text": "Fammi il punto su Prosperya"}).json()
    assert r["intent"] == "project_status"
    ctx = client.get(f"/api/v1/conversations/{r['conversation_id']}/context").json()
    assert ctx["last_intent"] == "project_status" and ctx["entity_name"] in ("Prosperya", "Prosperya CRM")
    msgs = client.get(f"/api/v1/conversations/{r['conversation_id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "nodo"]


def test_usage_telemetry(client):
    client.post("/api/v1/command/sync", json={"text": "mostrami i progetti"})
    u = client.get("/api/v1/telemetry/usage").json()
    assert u["local_requests"] >= 1 and u["estimated_cost"] == 0 and u["premium_requests"] == 0
    assert client.get("/api/v1/timeline").json()[0]["type"].startswith("request.")


def test_memory_facts_api(client):
    p = client.get("/api/v1/projects").json()[0]
    f = client.post("/api/v1/memory/facts", json={"subject_type": "project", "subject_id": p["id"],
                                                  "content": "Nuova sede aperta", "confidence": 0.9, "status": "INFERRED"})
    assert f.status_code == 201 and f.json()["status"] == "INFERRED"
    assert any(x["id"] == f.json()["id"] for x in client.get("/api/v1/memory/facts", params={"subject_id": p["id"]}).json())


def test_voice_flow(client):
    cfg = client.get("/api/v1/voice/config").json()
    assert cfg["push_to_talk"] and cfg["stt"]["name"] == "fake"
    s = client.post("/api/v1/voice/sessions").json()
    assert s["state"] == "IDLE"
    assert client.post(f"/api/v1/voice/sessions/{s['id']}/state", params={"state": "LISTENING"}).json()["state"] == "LISTENING"
    t = client.post("/api/v1/voice/transcribe", files={"file": ("a.webm", b"\x00\x01", "audio/webm")},
                    data={"session_id": s["id"]}).json()
    assert t["text"] == "fammi il punto su prosperya" and t["provider"] == "fake"
    assert client.get(f"/api/v1/voice/sessions/{s['id']}").json()["state"] == "UNDERSTANDING"
    r = client.post("/api/v1/command/sync", json={"text": t["text"], "channel": "voice"}).json()
    audio = client.post("/api/v1/voice/speak", data={"text": r["text"]})
    assert audio.status_code == 200 and audio.headers["X-NODO-TTS-Provider"] == "fake" and audio.content.startswith(b"RIFF")
    i = client.post(f"/api/v1/voice/sessions/{s['id']}/interrupt").json()
    assert i["state"] == "INTERRUPTED" and i["interrupted"]
    # illegal transition is rejected
    assert client.post(f"/api/v1/voice/sessions/{s['id']}/state", params={"state": "SPEAKING"}).status_code == 409
    # INTERRUPTED -> IDLE is legal
    assert client.post(f"/api/v1/voice/sessions/{s['id']}/state", params={"state": "IDLE"}).json()["state"] == "IDLE"


def test_voice_state_machine_unit():
    s = VoiceSession()
    for st in (VoiceState.LISTENING, VoiceState.TRANSCRIBING, VoiceState.UNDERSTANDING,
               VoiceState.THINKING, VoiceState.ACTING, VoiceState.SPEAKING):
        s.transition(st)
    s.transition(VoiceState.LISTENING)  # barge-in while speaking
    with pytest.raises(InvalidTransition):
        s.transition(VoiceState.SPEAKING)
    s.interrupt()
    assert s.state == VoiceState.INTERRUPTED and s.interrupted
    s.transition(VoiceState.LISTENING)  # after an interrupt NODO listens again
    s.transition(VoiceState.IDLE)
    s.transition(VoiceState.CLOSED)
    with pytest.raises(InvalidTransition):
        s.transition(VoiceState.IDLE)  # nothing escapes CLOSED


def test_auth_token_enforced(engine, container):
    from fastapi.testclient import TestClient

    from nodo.config import Settings
    from nodo.main import create_app
    st = Settings(api_token="secret")
    with TestClient(create_app(st, container)) as c:
        assert c.get("/api/v1/projects").status_code == 401
        assert c.get("/api/v1/projects", headers={"Authorization": "Bearer secret"}).status_code == 200
