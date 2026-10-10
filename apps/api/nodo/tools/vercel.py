"""Vercel connector. Normalizes Vercel projects/deployments/logs into NODO concepts; the Core never speaks the
Vercel API directly. Read actions are level 0; rollback/redeploy are level 3 (reversible: the previous deployment
stays available and can be promoted back at any time).

API references (REST, https://api.vercel.com): /v9/projects, /v6/deployments, /v13/deployments/{id},
/v3/deployments/{id}/events, /v1/projects/{id}/deployments/{id}/runtime-logs, /v9/projects/{id}/rollback/{dep},
/v10/projects/{id}/promote/{dep}, POST /v13/deployments (redeploy), /v2/user.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from nodo.core.permissions import ActionLevel
from nodo.tools.base import ToolAction, ToolSpec

READY, ERROR, BUILDING, QUEUED, CANCELED = "READY", "ERROR", "BUILDING", "QUEUED", "CANCELED"
IN_PROGRESS_STATES = {"BUILDING", "QUEUED", "INITIALIZING"}


@dataclass
class VercelProject:
    id: str
    name: str
    framework: str | None = None
    repo_owner: str | None = None
    repo_name: str | None = None
    production_branch: str = "main"
    domains: list[str] = field(default_factory=list)     # custom domains first, then *.vercel.app
    production_deployment_id: str | None = None

    @property
    def primary_url(self) -> str:
        host = self.domains[0] if self.domains else f"{self.name}.vercel.app"
        return f"https://{host}"


@dataclass
class Deployment:
    id: str
    project_id: str
    url: str                       # full https URL of this immutable deployment
    state: str                     # READY | ERROR | BUILDING | QUEUED | INITIALIZING | CANCELED
    target: str | None             # "production" | None (preview)
    created_at: str                # ISO-8601 UTC
    commit_sha: str | None = None
    commit_message: str | None = None
    commit_ref: str | None = None  # git branch
    error_message: str | None = None


@dataclass
class RuntimeLogs:
    available: bool
    errors: list[dict] = field(default_factory=list)   # {message, path, status, at}
    scanned: int = 0
    note: str | None = None


class VercelError(RuntimeError):
    pass


class VercelConnector(Protocol):
    async def whoami(self) -> dict: ...

    async def list_projects(self) -> list[VercelProject]: ...

    async def get_project(self, project_id: str) -> VercelProject: ...

    async def list_deployments(self, project_id: str, *, target: str | None = "production", limit: int = 10,
                               branch: str | None = None) -> list[Deployment]: ...

    async def get_deployment(self, deployment_id: str) -> Deployment: ...

    async def build_log(self, deployment_id: str, limit: int = 300) -> list[str]: ...

    async def runtime_errors(self, project_id: str, deployment_id: str, seconds: float = 6.0) -> RuntimeLogs: ...

    async def rollback(self, project_id: str, deployment_id: str) -> dict: ...

    async def promote(self, project_id: str, deployment_id: str) -> dict: ...

    async def redeploy(self, project_name: str, deployment_id: str) -> Deployment: ...


def _iso(ms: int | float | None) -> str:
    if not ms:
        return datetime.now(UTC).isoformat()
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _url(u: str | None) -> str:
    if not u:
        return ""
    return u if u.startswith("http") else f"https://{u}"


class HttpVercelConnector:
    def __init__(self, token: str | None, team_id: str | None = None, base_url: str = "https://api.vercel.com",
                 timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base, self.team_id, self.timeout, self.transport = base_url, team_id, timeout, transport
        self.headers = {"User-Agent": "nodo-core"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.token = token

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, headers=self.headers, timeout=timeout or self.timeout,
                                 transport=self.transport)

    def _params(self, params: dict | None = None) -> dict:
        p = {k: v for k, v in (params or {}).items() if v is not None}
        if self.team_id:
            p["teamId"] = self.team_id
        return p

    async def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        if not self.token:
            raise VercelError("NODO_VERCEL_TOKEN non configurato")
        async with self._client() as c:
            r = await c.request(method, path, params=self._params(params), json=body)
        if r.status_code >= 400:
            raise VercelError(f"Vercel {method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def whoami(self) -> dict:
        d = await self._req("GET", "/v2/user")
        u = d.get("user") or {}
        return {"username": u.get("username"), "email": u.get("email"), "team_id": self.team_id}

    @staticmethod
    def _project(p: dict) -> VercelProject:
        link = p.get("link") or {}
        prod = (p.get("targets") or {}).get("production") or {}
        aliases = [a if isinstance(a, str) else a.get("domain") for a in (prod.get("alias") or p.get("alias") or [])]
        aliases = [a for a in aliases if a]
        custom = [a for a in aliases if not a.endswith(".vercel.app")]
        vapp = [a for a in aliases if a.endswith(".vercel.app")]
        owner = link.get("org") or link.get("owner")
        return VercelProject(
            id=p["id"], name=p.get("name", p["id"]), framework=p.get("framework"),
            repo_owner=owner if link.get("type") == "github" else None,
            repo_name=link.get("repo") if link.get("type") == "github" else None,
            production_branch=link.get("productionBranch") or "main",
            domains=custom + vapp, production_deployment_id=prod.get("id"))

    async def list_projects(self) -> list[VercelProject]:
        out: list[VercelProject] = []
        params: dict = {"limit": 100}
        for _ in range(10):  # pagination guard
            d = await self._req("GET", "/v9/projects", params)
            out += [self._project(p) for p in d.get("projects", [])]
            nxt = (d.get("pagination") or {}).get("next")
            if not nxt:
                break
            params = {"limit": 100, "until": nxt}
        return out

    async def get_project(self, project_id: str) -> VercelProject:
        return self._project(await self._req("GET", f"/v9/projects/{project_id}"))

    @staticmethod
    def _deployment(d: dict, project_id: str | None = None) -> Deployment:
        meta = d.get("meta") or {}
        return Deployment(
            id=d.get("uid") or d.get("id"), project_id=d.get("projectId") or project_id or "",
            url=_url(d.get("url")), state=(d.get("readyState") or d.get("state") or "UNKNOWN").upper(),
            target=d.get("target"), created_at=_iso(d.get("createdAt") or d.get("created")),
            commit_sha=meta.get("githubCommitSha"), commit_message=((meta.get("githubCommitMessage") or "").strip().splitlines() or [""])[0][:200] or None,
            commit_ref=meta.get("githubCommitRef"), error_message=d.get("errorMessage"))

    async def list_deployments(self, project_id: str, *, target: str | None = "production", limit: int = 10,
                               branch: str | None = None) -> list[Deployment]:
        params = {"projectId": project_id, "limit": limit if not branch else max(limit, 30), "target": target}
        d = await self._req("GET", "/v6/deployments", params)
        deps = [self._deployment(x, project_id) for x in d.get("deployments", [])]
        if branch:
            deps = [x for x in deps if x.commit_ref == branch][:limit]
        return deps

    async def get_deployment(self, deployment_id: str) -> Deployment:
        return self._deployment(await self._req("GET", f"/v13/deployments/{deployment_id}"))

    async def build_log(self, deployment_id: str, limit: int = 300) -> list[str]:
        d = await self._req("GET", f"/v3/deployments/{deployment_id}/events",
                            {"builds": 1, "limit": limit, "direction": "backward"})
        events = d if isinstance(d, list) else d.get("events", [])
        rows: list[tuple[float, int, str]] = []
        for i, e in enumerate(events):
            payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            text = payload.get("text") or e.get("text")
            if text:
                rows.append((e.get("created") or payload.get("date") or 0, i, str(text).rstrip()))
        rows.sort()  # chronological, stable for events without timestamps
        return [t for _, _, t in rows][-limit:]

    async def runtime_errors(self, project_id: str, deployment_id: str, seconds: float = 6.0) -> RuntimeLogs:
        """Best effort: the runtime-logs endpoint streams recent lines (NDJSON). We read for a few seconds."""
        if not self.token:
            return RuntimeLogs(False, note="token mancante")
        res = RuntimeLogs(True)
        deadline = time.monotonic() + seconds
        try:
            async with self._client(timeout=seconds + 5) as c:
                async with c.stream("GET", f"/v1/projects/{project_id}/deployments/{deployment_id}/runtime-logs",
                                    params=self._params()) as r:
                    if r.status_code >= 400:
                        return RuntimeLogs(False, note=f"runtime logs non disponibili (HTTP {r.status_code})")
                    async for line in r.aiter_lines():
                        if time.monotonic() > deadline:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        res.scanned += 1
                        status = e.get("responseStatusCode") or 0
                        if (e.get("level") in ("error", "fatal")) or (isinstance(status, int) and status >= 500):
                            res.errors.append({"message": str(e.get("message", ""))[:500], "path": e.get("requestPath"),
                                               "status": status or None, "at": _iso(e.get("timestampInMs"))})
        except (TimeoutError, httpx.HTTPError) as e:
            if not res.scanned:
                return RuntimeLogs(False, note=f"runtime logs non raggiungibili: {type(e).__name__}")
        return res

    async def rollback(self, project_id: str, deployment_id: str) -> dict:
        try:
            await self._req("POST", f"/v9/projects/{project_id}/rollback/{deployment_id}")
            return {"ok": True, "method": "rollback", "deployment_id": deployment_id}
        except VercelError as first:
            try:  # promote is the general "point production at this deployment" operation
                await self._req("POST", f"/v10/projects/{project_id}/promote/{deployment_id}")
                return {"ok": True, "method": "promote", "deployment_id": deployment_id}
            except VercelError as second:
                raise VercelError(f"rollback fallito ({first}); promote fallito ({second})")

    async def promote(self, project_id: str, deployment_id: str) -> dict:
        """Serve this (newer) deployment on the production domains. Needed after an Instant Rollback, because Vercel
        then stops auto-assigning production domains to new deployments until something is promoted."""
        await self._req("POST", f"/v10/projects/{project_id}/promote/{deployment_id}")
        return {"ok": True, "method": "promote", "deployment_id": deployment_id}

    async def redeploy(self, project_name: str, deployment_id: str) -> Deployment:
        d = await self._req("POST", "/v13/deployments", {"forceNew": 1},
                            {"name": project_name, "deploymentId": deployment_id, "target": "production"})
        return self._deployment(d)


def vercel_tool(connector: VercelConnector) -> ToolSpec:
    async def list_projects() -> dict:
        return {"projects": [asdict(p) | {"primary_url": p.primary_url} for p in await connector.list_projects()]}

    async def deployments(project_id: str, target: str | None = "production", limit: int = 10,
                          branch: str | None = None) -> dict:
        deps = await connector.list_deployments(project_id, target=target, limit=limit, branch=branch)
        return {"deployments": [asdict(d) for d in deps]}

    async def build_log(deployment_id: str, limit: int = 300) -> dict:
        return {"lines": await connector.build_log(deployment_id, limit)}

    async def runtime_errors(project_id: str, deployment_id: str, seconds: float = 6.0) -> dict:
        return asdict(await connector.runtime_errors(project_id, deployment_id, seconds))

    async def rollback(project_id: str, deployment_id: str) -> dict:
        return await connector.rollback(project_id, deployment_id)

    async def redeploy(project_name: str, deployment_id: str) -> dict:
        return asdict(await connector.redeploy(project_name, deployment_id))

    async def promote(project_id: str, deployment_id: str) -> dict:
        return await connector.promote(project_id, deployment_id)

    return ToolSpec("vercel", "Vercel projects, deployments, logs, rollback", auth="token", actions={
        "list_projects": ToolAction("list_projects", ActionLevel.READ, "Projects with linked repo and domains", list_projects),
        "deployments": ToolAction("deployments", ActionLevel.READ, "Recent deployments", deployments),
        "build_log": ToolAction("build_log", ActionLevel.READ, "Build output of a deployment", build_log),
        "runtime_errors": ToolAction("runtime_errors", ActionLevel.READ, "Recent runtime errors", runtime_errors),
        "rollback": ToolAction("rollback", ActionLevel.EXECUTE_REVERSIBLE,
                               "Point production back to a previous deployment", rollback),
        "promote": ToolAction("promote", ActionLevel.EXECUTE_REVERSIBLE,
                              "Serve a newer deployment on the production domains", promote),
        "redeploy": ToolAction("redeploy", ActionLevel.EXECUTE_REVERSIBLE, "Rebuild a deployment for production", redeploy),
    })
