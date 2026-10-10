"""GitHub code operations used by the night shift: read files, compare commits, open/merge/close fix PRs,
dependency alerts. Separate from the read-only snapshot connector so each can be faked independently.

Writes never touch the default branch directly: fixes go to a new `nodo/fix-*` branch as ONE commit (git data
API) and reach production only through a pull request that a human approves.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class GitHubError(RuntimeError):
    pass


@dataclass
class FileChange:
    path: str
    status: str          # added | modified | removed | renamed
    patch: str | None = None


class GitHubRepoClient(Protocol):
    async def whoami(self) -> dict: ...

    async def tree(self, owner: str, name: str, ref: str) -> list[str]: ...

    async def get_file(self, owner: str, name: str, path: str, ref: str) -> str | None: ...

    async def compare(self, owner: str, name: str, base: str, head: str) -> list[FileChange]: ...

    async def recent_commits(self, owner: str, name: str, branch: str, n: int = 5) -> list[dict]: ...

    async def commit_files(self, owner: str, name: str, base_branch: str, new_branch: str,
                           files: dict[str, str], message: str) -> dict: ...

    async def open_pr(self, owner: str, name: str, head: str, base: str, title: str, body: str) -> dict: ...

    async def pr_state(self, owner: str, name: str, number: int) -> dict: ...

    async def merge_pr(self, owner: str, name: str, number: int, method: str = "squash") -> dict: ...

    async def close_pr(self, owner: str, name: str, number: int, comment: str | None = None) -> dict: ...

    async def delete_branch(self, owner: str, name: str, branch: str) -> dict: ...

    async def dependency_alerts(self, owner: str, name: str) -> list[dict] | None: ...


class HttpGitHubRepoClient:
    def __init__(self, token: str | None, base_url: str = "https://api.github.com", timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.token, self.base, self.timeout, self.transport = token, base_url, timeout, transport
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": "nodo-core",
                        "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def _req(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                   ok404: bool = False) -> Any:
        if not self.token:
            raise GitHubError("NODO_GITHUB_TOKEN non configurato")
        async with httpx.AsyncClient(base_url=self.base, headers=self.headers, timeout=self.timeout,
                                     transport=self.transport) as c:
            r = await c.request(method, path, params=params, json=body)
        if r.status_code == 404 and ok404:
            return None
        if r.status_code >= 400:
            raise GitHubError(f"GitHub {method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def whoami(self) -> dict:
        u = await self._req("GET", "/user")
        return {"login": u.get("login"), "name": u.get("name")}

    async def tree(self, owner: str, name: str, ref: str) -> list[str]:
        d = await self._req("GET", f"/repos/{owner}/{name}/git/trees/{ref}", params={"recursive": "1"})
        return [t["path"] for t in d.get("tree", []) if t.get("type") == "blob"]

    async def get_file(self, owner: str, name: str, path: str, ref: str) -> str | None:
        d = await self._req("GET", f"/repos/{owner}/{name}/contents/{path}", params={"ref": ref}, ok404=True)
        if not d or isinstance(d, list) or d.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(d["content"]).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None  # binary file: never edited by NODO

    async def compare(self, owner: str, name: str, base: str, head: str) -> list[FileChange]:
        d = await self._req("GET", f"/repos/{owner}/{name}/compare/{base}...{head}")
        return [FileChange(f["filename"], f.get("status", "modified"), f.get("patch")) for f in d.get("files", [])]

    async def recent_commits(self, owner: str, name: str, branch: str, n: int = 5) -> list[dict]:
        d = await self._req("GET", f"/repos/{owner}/{name}/commits", params={"sha": branch, "per_page": n}) or []
        return [{"sha": c["sha"], "message": c["commit"]["message"].splitlines()[0][:200],
                 "date": c["commit"]["committer"]["date"]} for c in d]

    async def commit_files(self, owner: str, name: str, base_branch: str, new_branch: str,
                           files: dict[str, str], message: str) -> dict:
        ref = await self._req("GET", f"/repos/{owner}/{name}/git/ref/heads/{base_branch}")
        base_sha = ref["object"]["sha"]
        base_commit = await self._req("GET", f"/repos/{owner}/{name}/git/commits/{base_sha}")
        tree = await self._req("POST", f"/repos/{owner}/{name}/git/trees", body={
            "base_tree": base_commit["tree"]["sha"],
            "tree": [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()]})
        commit = await self._req("POST", f"/repos/{owner}/{name}/git/commits", body={
            "message": message, "tree": tree["sha"], "parents": [base_sha]})
        await self._req("POST", f"/repos/{owner}/{name}/git/refs", body={"ref": f"refs/heads/{new_branch}",
                                                                          "sha": commit["sha"]})
        return {"branch": new_branch, "sha": commit["sha"], "base_sha": base_sha}

    async def open_pr(self, owner: str, name: str, head: str, base: str, title: str, body: str) -> dict:
        d = await self._req("POST", f"/repos/{owner}/{name}/pulls", body={"title": title, "head": head, "base": base,
                                                                           "body": body})
        return {"number": d["number"], "url": d.get("html_url"), "head_sha": (d.get("head") or {}).get("sha")}

    async def pr_state(self, owner: str, name: str, number: int) -> dict:
        d = await self._req("GET", f"/repos/{owner}/{name}/pulls/{number}")
        return {"state": d.get("state"), "merged": bool(d.get("merged")), "mergeable": d.get("mergeable"),
                "head_sha": (d.get("head") or {}).get("sha")}

    async def merge_pr(self, owner: str, name: str, number: int, method: str = "squash") -> dict:
        d = await self._req("PUT", f"/repos/{owner}/{name}/pulls/{number}/merge", body={"merge_method": method})
        return {"merged": bool(d.get("merged")), "sha": d.get("sha"), "message": d.get("message")}

    async def close_pr(self, owner: str, name: str, number: int, comment: str | None = None) -> dict:
        if comment:
            await self._req("POST", f"/repos/{owner}/{name}/issues/{number}/comments", body={"body": comment})
        d = await self._req("PATCH", f"/repos/{owner}/{name}/pulls/{number}", body={"state": "closed"})
        return {"state": d.get("state")}

    async def delete_branch(self, owner: str, name: str, branch: str) -> dict:
        await self._req("DELETE", f"/repos/{owner}/{name}/git/refs/heads/{branch}", ok404=True)
        return {"deleted": branch}

    async def dependency_alerts(self, owner: str, name: str) -> list[dict] | None:
        """Open Dependabot alerts, or None when the token lacks the permission / alerts are disabled."""
        try:
            d = await self._req("GET", f"/repos/{owner}/{name}/dependabot/alerts",
                                params={"state": "open", "per_page": 100})
        except GitHubError as e:
            if any(code in str(e) for code in ("HTTP 403", "HTTP 404")):
                return None
            raise
        out = []
        for a in d or []:
            adv = a.get("security_advisory") or {}
            dep = (a.get("dependency") or {}).get("package") or {}
            out.append({"severity": (adv.get("severity") or "unknown").lower(), "package": dep.get("name"),
                        "summary": (adv.get("summary") or "")[:200], "url": a.get("html_url")})
        return out
