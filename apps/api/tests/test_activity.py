"""Activity feed: the panel's window onto what NODO really does."""
from __future__ import annotations

from nodo.db.models import EventLog


def test_activity_feed_returns_real_events(client, db, seeded):
    db.add(EventLog(event_type="agent.work", organization_id=seeded,
                    payload={"tool": "web_search", "ok": True}))
    db.add(EventLog(event_type="approval.requested", organization_id=seeded,
                    payload={"label": "pubblicare su tiktok: «promo»"}))
    db.flush()
    r = client.get("/api/v1/activity")
    assert r.status_code == 200
    events = r.json()["events"]
    kinds = {e["kind"] for e in events}
    assert "tool" in kinds and "decision" in kinds
    whats = [e["what"] for e in events]
    assert any("web_search" in w for w in whats)
    assert any("pubblicare su tiktok" in w for w in whats)


def test_activity_since_filters(client, db, seeded):
    first = client.get("/api/v1/activity").json()
    db.add(EventLog(event_type="agent.work", organization_id=seeded,
                    payload={"tool": "recall", "ok": True}))
    db.flush()
    fresh = client.get("/api/v1/activity", params={"since": first["now"]}).json()
    whats = [e["what"] for e in fresh["events"]]
    assert any("recall" in w for w in whats)
