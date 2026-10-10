"""End to end through the REAL HTTP connectors: the night shift talks to Vercel/GitHub REST shapes served by a
mock transport backed by the simulated world. Catches mismatches between connectors and the shift logic."""
from __future__ import annotations

import base64
import json
import re

import httpx
from sqlalchemy import select

from nodo.db.models import FixProposal, Incident, NightAction, Site
from nodo.night import approvals as A
from nodo.night.simulation import build_demo_world
from nodo.tools.github_repo import HttpGitHubRepoClient
from nodo.tools.vercel import HttpVercelConnector
from tests.test_night import drain_background, make_container


def _ms(iso: str) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def vercel_api(world):
    v = world.vercel

    def dep(d):
        return {"uid": d.id, "id": d.id, "url": d.url.removeprefix("https://"), "state": d.state,
                "readyState": d.state, "target": d.target, "created": _ms(d.created_at), "createdAt": _ms(d.created_at),
                "projectId": d.project_id, "errorMessage": d.error_message,
                "meta": {"githubCommitSha": d.commit_sha, "githubCommitMessage": d.commit_message,
                         "githubCommitRef": d.commit_ref}}

    def proj(p):
        return {"id": p.id, "name": p.name, "framework": "nextjs",
                "link": {"type": "github", "org": p.repo_owner, "repo": p.repo_name, "productionBranch": p.production_branch},
                "targets": {"production": {"id": p.production_deployment_id, "alias": p.domains}}}

    def handle(req: httpx.Request) -> httpx.Response:
        p, q = req.url.path, req.url.params
        if p == "/v2/user":
            return httpx.Response(200, json={"user": {"username": "gaetano", "defaultTeamId": "team_x"}})
        assert q.get("teamId") == "team_x", f"missing team on {p}"
        if p == "/v9/projects":
            return httpx.Response(200, json={"projects": [proj(x) for x in v.projects.values()], "pagination": {}})
        if m := re.fullmatch(r"/v9/projects/([^/]+)", p):
            return httpx.Response(200, json=proj(v.projects[m.group(1)]))
        if p == "/v6/deployments":
            deps = [d for d in v.deployments.values() if d.project_id == q["projectId"]
                    and ("target" not in q or d.target == q["target"])]
            deps.sort(key=lambda d: d.created_at, reverse=True)
            return httpx.Response(200, json={"deployments": [dep(d) for d in deps[: int(q.get("limit", 20))]]})
        if (m := re.fullmatch(r"/v13/deployments/([^/]+)", p)) and req.method == "GET":
            return httpx.Response(200, json=dep(v.deployments[m.group(1)]))
        if m := re.fullmatch(r"/v3/deployments/([^/]+)/events", p):
            lines = v.build_logs.get(m.group(1), [])
            return httpx.Response(200, json=[{"type": "stdout", "created": i, "payload": {"text": t}}
                                             for i, t in enumerate(lines)])
        if m := re.fullmatch(r"/v1/projects/([^/]+)/deployments/([^/]+)/runtime-logs", p):
            rows = [{"level": "error", "message": e["message"], "requestPath": e.get("path"),
                     "responseStatusCode": e.get("status")} for e in v.runtime.get(m.group(2), [])]
            return httpx.Response(200, content="\n".join(json.dumps(r) for r in rows).encode())
        if m := re.fullmatch(r"/v1/projects/([^/]+)/rollback/([^/]+)", p):
            v.projects[m.group(1)].production_deployment_id = m.group(2)
            v.auto_assign[m.group(1)] = False
            v.calls.append(f"rollback:{m.group(1)}:{m.group(2)}")
            return httpx.Response(201)
        if m := re.fullmatch(r"/v10/projects/([^/]+)/promote/([^/]+)", p):
            v.projects[m.group(1)].production_deployment_id = m.group(2)
            v.auto_assign[m.group(1)] = True
            v.calls.append(f"promote:{m.group(1)}:{m.group(2)}")
            return httpx.Response(201)
        if p == "/v13/deployments" and req.method == "POST":
            body = json.loads(req.content)
            old = v.deployments[body["deploymentId"]]
            new = v.deploy(old.project_id, sha=old.commit_sha, message=old.commit_message, ref=old.commit_ref)
            v.calls.append(f"redeploy:{old.id}")
            return httpx.Response(200, json=dep(new))
        return httpx.Response(404, json={"error": {"message": f"no route {req.method} {p}"}})
    return httpx.MockTransport(handle)


def github_api(world):
    g = world.github
    trees: dict[str, dict] = {}
    commits: dict[str, dict] = {}

    def handle(req: httpx.Request) -> httpx.Response:
        p, q = req.url.path, req.url.params
        body = json.loads(req.content) if req.content else {}
        if p == "/user":
            return httpx.Response(200, json={"login": "gaetano"})
        m = re.match(r"/repos/([^/]+)/([^/]+)(/.*)$", p)
        owner, name, rest = m.groups()
        r = g.repos[f"{owner}/{name}"]
        files_at = lambda ref: r["commits"][r["branches"].get(ref, ref)]["files"]  # noqa: E731
        if mm := re.fullmatch(r"/git/trees/(.+)", rest):
            if req.method == "GET":
                return httpx.Response(200, json={"tree": [{"path": x, "type": "blob"} for x in files_at(mm.group(1))]})
        if rest == "/git/trees" and req.method == "POST":
            sha = f"tree{len(trees)}"
            trees[sha] = {"base": body["base_tree"], "files": {e["path"]: e["content"] for e in body["tree"]}}
            return httpx.Response(201, json={"sha": sha})
        if mm := re.fullmatch(r"/contents/(.+)", rest):
            content = files_at(q["ref"]).get(mm.group(1))
            if content is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(content.encode()).decode()})
        if mm := re.fullmatch(r"/compare/(.+)\.\.\.(.+)", rest):
            changes = [{"filename": x, "status": "modified"} for x in
                       sorted(set(files_at(mm.group(1))) | set(files_at(mm.group(2))))
                       if files_at(mm.group(1)).get(x) != files_at(mm.group(2)).get(x)]
            return httpx.Response(200, json={"files": changes})
        if mm := re.fullmatch(r"/git/ref/heads/(.+)", rest):
            return httpx.Response(200, json={"object": {"sha": r["branches"][mm.group(1)]}})
        if mm := re.fullmatch(r"/git/commits/(.+)", rest):
            return httpx.Response(200, json={"tree": {"sha": mm.group(1)}})  # tree sha == commit sha here
        if rest == "/git/commits" and req.method == "POST":
            sha = f"c{len(commits)}"
            commits[sha] = {"tree": body["tree"], "parent": body["parents"][0], "message": body["message"]}
            return httpx.Response(201, json={"sha": sha})
        if rest == "/git/refs" and req.method == "POST":
            c = commits[body["sha"]]
            branch = body["ref"].removeprefix("refs/heads/")
            r["branches"][branch] = c["parent"]
            new_sha = g.commit(owner, name, branch, trees[c["tree"]]["files"], c["message"])
            g.vercel.on_push(owner, name, branch, new_sha, c["message"], r["commits"][new_sha]["files"])
            return httpx.Response(201, json={})
        if rest == "/pulls" and req.method == "POST":
            n = len(g.prs) + 1
            g.prs[(f"{owner}/{name}", n)] = {"head": body["head"], "base": body["base"], "title": body["title"],
                                              "state": "open", "merged": False}
            return httpx.Response(201, json={"number": n, "html_url": f"https://github.com/{owner}/{name}/pull/{n}"})
        if mm := re.fullmatch(r"/pulls/(\d+)/merge", rest):
            import asyncio  # noqa: F401  (merge logic lives in the fake: reuse it synchronously)
            pr = g.prs[(f"{owner}/{name}", int(mm.group(1)))]
            head_files = files_at(pr["head"])
            sha = g.commit(owner, name, pr["base"], head_files, pr["title"])
            pr.update(state="closed", merged=True)
            g.vercel.on_merge(owner, name, sha, pr["title"], head_files)
            return httpx.Response(200, json={"merged": True, "sha": sha})
        if mm := re.fullmatch(r"/pulls/(\d+)", rest):
            pr = g.prs[(f"{owner}/{name}", int(mm.group(1)))]
            return httpx.Response(200, json={"state": pr["state"], "merged": pr["merged"]})
        if rest == "/dependabot/alerts":
            return httpx.Response(200, json=[{"security_advisory": {"severity": a["severity"], "summary": a["summary"]},
                                              "dependency": {"package": {"name": a["package"]}}}
                                             for a in g.alerts.get(f"{owner}/{name}", [])])
        return httpx.Response(404, json={"message": f"no route {req.method} {p}"})
    return httpx.MockTransport(handle)


async def test_night_through_http_connectors(db, seeded, settings):
    world = build_demo_world()
    c = make_container(world, settings)
    c.vercel = HttpVercelConnector("tok", transport=vercel_api(world))
    c.repo = HttpGitHubRepoClient("tok", transport=github_api(world))
    shift = c.night_shift(db)
    await shift.discover()
    for s in db.scalars(select(Site)):
        if s.name == "pbcare-platform":
            s.key_pages = [{"path": "/contatti"}]
    await shift.run_pass("light", "test")
    world.break_things()
    run = await c.night_shift(db).run_pass("night", "test")
    assert run.status == "done", run.error
    acts = {(a.action, a.status) for a in db.scalars(select(NightAction))}
    assert ("rollback", "verified") in acts and ("redeploy", "verified") in acts and ("draft", "done") in acts
    fix = db.scalars(select(FixProposal)).one()
    assert fix.pr_number == 1 and fix.preview_status == "ok" and "data?.items" in fix.diff
    vuln = db.scalars(select(Incident).where(Incident.kind == "vulnerabilities")).one()
    assert vuln.status == "open"
    out = await A.resolve(db, c, fix.approval_id, approve=True)
    assert out["ok"], out
    db.commit()
    await drain_background()
    db.expire_all()
    assert db.scalars(select(NightAction).where(NightAction.action == "verify")).one().status == "verified"
    assert any(x.startswith("promote:") for x in world.vercel.calls)
