"""Pulse regression tests: grounded prioritization, per-org isolation and read-only access."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nodo.db.models import Approval, Incident, Organization, Project, Site, Task, TaskStatus
from nodo.pulse import build_pulse

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


def test_pulse_empty_database_does_not_claim_sites_healthy(db, settings):
    org = Organization(name="Solo", slug="pulse-solo")
    db.add(org)
    db.flush()
    pulse = build_pulse(db, org.id, settings, now=NOW)
    assert pulse["read_only"] is True
    assert pulse["counts"]["sites_checked_last_night"] == 0
    assert pulse["items"] == []
    assert "Non ho ancora" in pulse["headline"]
    assert pulse["night"]["runs"] == 0


def test_pulse_prioritizes_real_overdue_and_blocked_tasks(db, settings):
    settings.timezone = "Europe/Rome"
    org = Organization(name="Team", slug="pulse-team")
    db.add(org)
    db.flush()
    project = Project(organization_id=org.id, name="Prosperya", slug="prosperya",
                      status="active")
    db.add(project)
    db.flush()
    overdue = Task(organization_id=org.id, project_id=project.id, title="Correggere pagina",
                   due_date=NOW - timedelta(days=2), priority=1, status=TaskStatus.TODO)
    blocked = Task(organization_id=org.id, project_id=project.id, title="Attendere documenti",
                   status=TaskStatus.BLOCKED, blocked_reason="Manca la conferma", priority=3)
    later = Task(organization_id=org.id, project_id=project.id, title="Attività futura",
                 due_date=NOW + timedelta(days=30), priority=5, status=TaskStatus.TODO)
    db.add_all([overdue, blocked, later])
    db.flush()

    pulse = build_pulse(db, org.id, settings, now=NOW)
    assert pulse["counts"]["open_tasks"] == 3
    assert pulse["counts"]["overdue_tasks"] == 1
    assert pulse["counts"]["blocked_tasks"] == 1
    assert len(pulse["items"]) == 2
    assert pulse["items"][0]["source_id"] == overdue.id
    assert "Prosperya" in pulse["items"][0]["prompt"]
    assert pulse["items"][1]["source_id"] == blocked.id
    assert "Manca la conferma" in pulse["items"][1]["detail"]


def test_pulse_incident_and_approval_have_provenance_not_auto_actions(db, settings):
    org = Organization(name="Another Team", slug="pulse-team-2")
    db.add(org)
    db.flush()
    site = Site(organization_id=org.id, name="PB Care", url="https://example.test",
                status="critical", last_checked_at=NOW - timedelta(hours=2))
    approval = Approval(organization_id=org.id, request_id="req-pulse",
                        tool_name="github", action="merge", level=4, status="pending")
    db.add_all([site, approval])
    db.flush()
    incident = Incident(organization_id=org.id, site_id=site.id, fingerprint="down",
                        kind="site_down", severity="critical", title="Sito non raggiungibile",
                        status="open", last_seen_at=NOW - timedelta(hours=2))
    db.add(incident)
    db.flush()

    pulse = build_pulse(db, org.id, settings, now=NOW)
    assert pulse["counts"]["open_incidents"] == 1
    assert pulse["counts"]["pending_approvals"] == 1
    assert pulse["items"][0]["source"] == "incident"
    assert pulse["items"][0]["source_id"] == incident.id
    assert "Ultimo controllo" in pulse["items"][0]["detail"]
    assert pulse["items"][1]["source"] == "approval"
    assert pulse["items"][1]["requires_approval"] is True
    assert approval.status == "pending"  # reading Pulse never approves


def test_pulse_isolates_organizations(db, settings):
    a = Organization(name="First", slug="pulse-first")
    b = Organization(name="Second", slug="pulse-second")
    db.add_all([a, b])
    db.flush()
    db.add_all([
        Task(organization_id=a.id, title="Visibile", priority=1, status=TaskStatus.TODO),
        Task(organization_id=b.id, title="Segreto B", priority=1, status=TaskStatus.TODO),
        Approval(organization_id=b.id, request_id="req-B", tool_name="vercel",
                 action="rollback", level=4, status="pending"),
    ])
    db.flush()
    pulse = build_pulse(db, a.id, settings, now=NOW)
    assert pulse["counts"]["open_tasks"] == 1
    assert pulse["counts"]["pending_approvals"] == 0
    assert [x["title"] for x in pulse["items"]] == ["Visibile"]


def test_pulse_rest_endpoint_returns_read_only_radar(client):
    r = client.get("/api/v1/pulse")
    assert r.status_code == 200
    snapshot = r.json()
    assert snapshot["read_only"] is True
    assert "headline" in snapshot and "items" in snapshot
    assert "counts" in snapshot and "generated_at" in snapshot


def test_greeting_is_short_grounded_and_honest(db, seeded, settings):
    from datetime import UTC, datetime

    from nodo.greeting import build_greeting

    g = build_greeting(db, seeded, settings, now=datetime(2026, 10, 13, 6, 30, tzinfo=UTC))
    assert g["text"].startswith("Buongiorno Gaetano.") and g["text"].endswith("Da dove vuoi cominciare?")
    assert g["part_of_day"] == "morning" and len(g["text"]) < 700
    evening = build_greeting(db, seeded, settings, now=datetime(2026, 10, 13, 19, 0, tzinfo=UTC))
    assert evening["text"].startswith("Buonasera")


def test_greeting_endpoint(client):
    r = client.get("/api/v1/greeting")
    assert r.status_code == 200 and "Gaetano" in r.json()["text"]


def test_greeting_is_saved_in_the_conversation(client):
    from sqlalchemy import select

    from nodo.db.models import Message
    from nodo.db.session import session_scope

    r = client.post("/api/v1/greeting", json={}).json()
    assert r["conversation_id"] and r["text"].startswith(("Buongiorno", "Ciao", "Buon", "Buonasera"))
    with session_scope() as s:
        msgs = s.scalars(select(Message).where(Message.conversation_id == r["conversation_id"])).all()
        assert [m.role for m in msgs] == ["nodo"] and msgs[0].content == r["text"]
