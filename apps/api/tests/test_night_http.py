"""HTTP adapters of the night shift (request shapes + response parsing) and the night REST API."""
from __future__ import annotations

import json

import httpx
from sqlalchemy import select

from nodo.night.notify import TelegramNotifier
from nodo.night.probe import HttpProber
from nodo.providers.anthropic import AnthropicProvider
from nodo.providers.base import ChatMessage, collect
from nodo.tools.github_repo import HttpGitHubRepoClient
from nodo.tools.vercel import HttpVercelConnector


def mock(handler):
    calls: list[httpx.Request] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return handler(req)
    return httpx.MockTransport(wrapped), calls


# ------------------------------------------------------------------------------------------------ Vercel
async def test_vercel_projects_and_deployments():
    def h(req):
        assert req.headers["authorization"] == "Bearer tok" and req.url.params["teamId"] == "team_1"
        if req.url.path == "/v9/projects":
            return httpx.Response(200, json={"projects": [{
                "id": "prj_1", "name": "pbcare", "framework": "nextjs",
                "link": {"type": "github", "org": "acme", "repo": "pbcare", "productionBranch": "main"},
                "targets": {"production": {"id": "dpl_9", "alias": ["pbcare.vercel.app", "www.pbcare.it"]}}}],
                "pagination": {"next": None}})
        if req.url.path == "/v6/deployments":
            assert req.url.params["projectId"] == "prj_1" and req.url.params["target"] == "production"
            return httpx.Response(200, json={"deployments": [
                {"uid": "dpl_9", "url": "pbcare-abc.vercel.app", "state": "READY", "target": "production",
                 "created": 1760000000000, "meta": {"githubCommitSha": "abc", "githubCommitMessage": "fix: x\n\nbody",
                                                     "githubCommitRef": "main"}}]})
        return httpx.Response(404)
    t, _ = mock(h)
    v = HttpVercelConnector("tok", "team_1", transport=t)
    [p] = await v.list_projects()
    assert (p.repo_owner, p.repo_name, p.production_deployment_id) == ("acme", "pbcare", "dpl_9")
    assert p.primary_url == "https://www.pbcare.it"  # custom domain preferred over *.vercel.app
    [d] = await v.list_deployments("prj_1")
    assert d.url == "https://pbcare-abc.vercel.app" and d.commit_message == "fix: x" and d.state == "READY"


async def test_vercel_rollback_falls_back_to_promote_and_redeploy_body():
    def h(req):
        if "/rollback/" in req.url.path:
            return httpx.Response(400, json={"error": {"message": "not allowed"}})
        if "/promote/" in req.url.path:
            return httpx.Response(201, json={})
        if req.url.path == "/v13/deployments" and req.method == "POST":
            body = json.loads(req.content)
            assert body == {"name": "pbcare", "deploymentId": "dpl_1", "target": "production"}
            return httpx.Response(200, json={"id": "dpl_2", "url": "x.vercel.app", "readyState": "QUEUED"})
        return httpx.Response(404)
    t, calls = mock(h)
    v = HttpVercelConnector("tok", transport=t)
    out = await v.rollback("prj_1", "dpl_1")
    assert out["method"] == "promote" and [c.url.path for c in calls][-1] == "/v10/projects/prj_1/promote/dpl_1"
    d = await v.redeploy("pbcare", "dpl_1")
    assert d.id == "dpl_2" and d.state == "QUEUED"


async def test_vercel_runtime_logs_and_build_log():
    lines = [{"level": "info", "message": "ok", "responseStatusCode": 200},
             {"level": "error", "message": "TypeError: boom", "requestPath": "/contatti", "responseStatusCode": 500,
              "timestampInMs": 1760000000000}]

    def h(req):
        if req.url.path.endswith("/runtime-logs"):
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        if req.url.path.endswith("/events"):
            return httpx.Response(200, json=[{"type": "stderr", "created": 2, "payload": {"text": "Error: b"}},
                                             {"type": "stdout", "created": 1, "payload": {"text": "start a"}}])
        return httpx.Response(404)
    t, _ = mock(h)
    v = HttpVercelConnector("tok", transport=t)
    rt = await v.runtime_errors("prj_1", "dpl_1", seconds=2)
    assert rt.available and rt.scanned == 2 and rt.errors[0]["path"] == "/contatti"
    assert await v.build_log("dpl_1") == ["start a", "Error: b"]


# ------------------------------------------------------------------------------------------------ GitHub
async def test_github_fix_branch_is_one_commit_then_pr():
    seen = []

    def h(req):
        seen.append(f"{req.method} {req.url.path}")
        p = req.url.path
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base"}})
        if p.endswith("/git/commits/base"):
            return httpx.Response(200, json={"tree": {"sha": "tree0"}})
        if p.endswith("/git/trees"):
            body = json.loads(req.content)
            assert body["base_tree"] == "tree0" and body["tree"][0]["path"] == "src/a.ts"
            return httpx.Response(201, json={"sha": "tree1"})
        if p.endswith("/git/commits"):
            assert json.loads(req.content)["parents"] == ["base"]
            return httpx.Response(201, json={"sha": "c1"})
        if p.endswith("/git/refs"):
            assert json.loads(req.content) == {"ref": "refs/heads/nodo/fix-x", "sha": "c1"}
            return httpx.Response(201, json={})
        if p.endswith("/pulls"):
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/acme/s/pull/7"})
        return httpx.Response(404)
    t, _ = mock(h)
    gh = HttpGitHubRepoClient("tok", transport=t)
    c = await gh.commit_files("acme", "s", "main", "nodo/fix-x", {"src/a.ts": "x"}, "fix")
    pr = await gh.open_pr("acme", "s", "nodo/fix-x", "main", "t", "b")
    assert c["sha"] == "c1" and pr["number"] == 7
    assert not any(s.startswith("PUT") for s in seen)  # never writes the default branch directly


async def test_github_alerts_without_permission_is_none():
    t, _ = mock(lambda req: httpx.Response(403, json={"message": "Resource not accessible"}))
    assert await HttpGitHubRepoClient("tok", transport=t).dependency_alerts("a", "b") is None


# ------------------------------------------------------------------------------------------------ probes / AI / notify
async def test_http_prober():
    def h(req):
        if req.url.path == "/down":
            return httpx.Response(500, text="err")
        if req.url.path == "/preview":
            return httpx.Response(401, headers={"x-vercel-id": "fra1::x"}, text="Authentication Required")
        return httpx.Response(200, text="<html><title>Home PB</title><body>Contattaci</body></html>")
    t, _ = mock(h)
    p = HttpProber(transport=t)
    ok = await p.probe("https://x.it/", expect="contattaci")
    assert ok.ok and ok.title == "Home PB" and ok.expect_found
    miss = await p.probe("https://x.it/", expect="Prenota")
    assert not miss.ok and not miss.down and "Prenota" in miss.short()
    down = await p.probe("https://x.it/down")
    assert down.down and not down.ok
    prot = await p.probe("https://x.it/preview")
    assert prot.protected and not prot.down


async def test_anthropic_stream_parsing():
    sse = "\n".join([
        'event: message_start', 'data: {"type":"message_start","message":{"usage":{"input_tokens":12}}}', '',
        'event: content_block_delta', 'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Ciao"}}', '',
        'event: content_block_delta', 'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" Gaetano"}}', '',
        'event: message_delta', 'data: {"type":"message_delta","usage":{"output_tokens":3}}', ''])

    def h(req):
        body = json.loads(req.content)
        assert req.headers["x-api-key"] == "k" and body["system"] == "sys"
        assert [m["role"] for m in body["messages"]] == ["user"]  # consecutive user turns merged
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    t, _ = mock(h)
    p = AnthropicProvider("k", "claude-sonnet-4-5", transport=t)
    res = await collect(p.stream("claude-sonnet-4-5", [ChatMessage("system", "sys"), ChatMessage("user", "a"),
                                                       ChatMessage("user", "b")]))
    assert res.text == "Ciao Gaetano" and res.tokens_in == 12 and res.tokens_out == 3
    assert p.models()[0].coding == 5 and p.models()[0].is_paid


async def test_telegram_notifier_chunks():
    t, calls = mock(lambda req: httpx.Response(200, json={"ok": True}))
    n = TelegramNotifier("123:abc", "42", transport=t)
    assert await n.send("x" * 5000)
    assert len(calls) == 2 and json.loads(calls[0].content)["chat_id"] == "42"


# ------------------------------------------------------------------------------------------------ REST API
def _night_client(engine, settings):
    from fastapi.testclient import TestClient

    from nodo.main import create_app
    from nodo.night.simulation import build_demo_world
    from tests.test_night import make_container
    world = build_demo_world()
    c = make_container(world, settings)
    return TestClient(create_app(settings, c)), world, c


def test_night_api_end_to_end(engine, settings):
    from nodo.db.session import session_scope
    from nodo.seed import seed_demo
    client, world, c = _night_client(engine, settings)
    with client:
        with session_scope() as s:
            seed_demo(s)
        assert client.post("/api/v1/sites/discover").json()["created"]
        sites = client.get("/api/v1/sites").json()
        pb = next(s for s in sites if s["name"] == "pbcare-platform")
        client.patch(f"/api/v1/sites/{pb['id']}", json={"key_pages": [{"path": "/contatti", "expect": None}]})
        r = client.post("/api/v1/night/run", json={"kind": "light", "wait": True}).json()
        assert r["status"] == "done" and r["summary"]["ok"] == 3
        world.break_things()
        r = client.post("/api/v1/night/run", json={"kind": "night", "wait": True}).json()
        assert r["status"] == "done" and r["summary"]["actions"]["rollback"] == 1
        detail = client.get(f"/api/v1/night/runs/{r['run_id']}").json()
        assert any(a["action"] == "fix_pr" for a in detail["actions"]) and detail["checks"]
        b = client.get("/api/v1/night/briefing").json()
        assert "PR #" in b["text"] and len(b["pending"]) == 1
        assert client.get("/api/v1/incidents").json()
        fixes = client.get("/api/v1/fixes").json()
        assert fixes[0]["status"] == "proposed" and fixes[0]["preview_status"] == "ok" and fixes[0]["diff"]
        out = client.post(f"/api/v1/fixes/{fixes[0]['id']}/approve").json()
        assert out["status"] == "approved" and out["ok"]
        assert client.get("/api/v1/fixes").json()[0]["status"] == "merged"
        assert client.post(f"/api/v1/fixes/{fixes[0]['id']}/approve").status_code == 409
        st = client.get("/api/v1/night/status").json()
        assert st["connectors"]["vercel"] == "FakeVercel" and st["policy"]["auto_rollback"] is True
        chk = client.get("/api/v1/setup/check").json()
        assert {x["name"] for x in chk["checks"]} >= {"Vercel", "GitHub", "AI per le correzioni"}
        assert next(x for x in chk["checks"] if x["name"] == "Vercel")["status"] == "ok"
        site = client.post("/api/v1/sites", json={"name": "Landing", "url": "https://landing.example.it",
                                                  "key_pages": [{"path": "/prenota", "expect": "Prenota"}]})
        assert site.status_code == 201 and site.json()["status"] == "unknown"
        assert client.post("/api/v1/sites", json={"name": "x", "url": "ftp://x"}).status_code == 422


def test_generic_approval_endpoint_executes(engine, settings):
    """POST /approvals/{id}/approve now executes the stored action (Phase 4)."""
    from nodo.config import Settings
    from nodo.db.models import NightAction
    from nodo.db.session import session_scope
    from nodo.seed import seed_demo
    client, world, c = _night_client(engine, Settings(night_auto_rollback=False, night_fix_prs=False))
    with client:
        with session_scope() as s:
            seed_demo(s)
        client.post("/api/v1/sites/discover")
        client.post("/api/v1/night/run", json={"kind": "light", "wait": True})
        world.break_things()
        client.post("/api/v1/night/run", json={"kind": "light", "wait": True})
        pending = client.get("/api/v1/approvals").json()
        rb = next(a for a in pending if a["action"] == "rollback")
        out = client.post(f"/api/v1/approvals/{rb['id']}/approve").json()
        assert out["status"] == "approved" and out["result"]["executed"]
        assert any(x.startswith("rollback") for x in world.vercel.calls)
        with session_scope() as s:
            act = s.scalars(select(NightAction).where(NightAction.approval_id == rb["id"])).one()
            assert act.status == "done"


async def test_vercel_uses_default_team_when_not_configured():
    def h(req):
        if req.url.path == "/v2/user":
            assert "teamId" not in req.url.params
            return httpx.Response(200, json={"user": {"username": "g", "defaultTeamId": "team_def"}})
        assert req.url.params.get("teamId") == "team_def"
        return httpx.Response(200, json={"projects": []})
    t, calls = mock(h)
    v = HttpVercelConnector("tok", transport=t)
    assert await v.list_projects() == [] and await v.list_projects() == []
    assert [c.url.path for c in calls].count("/v2/user") == 1
