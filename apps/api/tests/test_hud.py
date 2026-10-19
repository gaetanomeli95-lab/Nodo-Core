from datetime import UTC, datetime


def test_hud_tells_only_measured_facts(client, db):
    from nodo.db.models import InferenceRun
    db.add(InferenceRun(request_id="r1", provider="groq", model="qwen", purpose="brain", latency_ms=820, status="ok",
                        created_at=datetime.now(UTC)))
    db.add(InferenceRun(request_id="r2", provider="nvidia", model="ds", purpose="brain", latency_ms=1800,
                        status="error", created_at=datetime.now(UTC)))
    db.commit()
    r = client.get("/api/v1/hud").json()
    assert r["runs"] == 2 and r["errors"] == 1 and r["avg_ms"] == 1310
    assert r["brain"]["provider"] in ("groq", "nvidia") and "ms" in r["brain"]
    assert r["jobs"] == 0
