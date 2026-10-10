"""GitHub connector. Normalizes GitHub data into NODO concepts (RepoSnapshot). The Core never speaks GitHub API."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from nodo.core.permissions import ActionLevel
from nodo.tools.base import ToolAction, ToolSpec
from nodo.tools.github_repo import GitHubRepoClient


@dataclass
class RepoSnapshot:
    owner: str
    name: str
    default_branch: str
    fetched_at: str
    commits_last_7d: int = 0
    last_commit_at: str | None = None
    last_commit_message: str | None = None
    open_prs: int = 0
    open_issues: int = 0
    ci_status: str | None = None  # success | failure | pending | unknown
    active_branches: list[str] = field(default_factory=list)
    freshness: str = "live"       # live | cached | unavailable
    error: str | None = None


class GitHubConnector(Protocol):
    async def snapshot(self, owner: str, name: str, default_branch: str = "main") -> RepoSnapshot: ...


class HttpGitHubConnector:
    def __init__(self, token: str | None, base_url: str = "https://api.github.com", timeout: float = 15.0):
        self.base, self.timeout = base_url, timeout
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": "nodo-core"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def snapshot(self, owner: str, name: str, default_branch: str = "main") -> RepoSnapshot:
        snap = RepoSnapshot(owner, name, default_branch, datetime.now(UTC).isoformat())
        since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        async with httpx.AsyncClient(base_url=self.base, headers=self.headers, timeout=self.timeout) as c:
            try:
                commits = (await _get(c, f"/repos/{owner}/{name}/commits", params={"since": since, "per_page": 100})) or []
                snap.commits_last_7d = len(commits)
                latest = (await _get(c, f"/repos/{owner}/{name}/commits", params={"per_page": 1})) or []
                if latest:
                    snap.last_commit_at = latest[0]["commit"]["committer"]["date"]
                    snap.last_commit_message = latest[0]["commit"]["message"].splitlines()[0][:200]
                prs = (await _get(c, f"/repos/{owner}/{name}/pulls", params={"state": "open", "per_page": 50})) or []
                snap.open_prs = len(prs)
                issues = (await _get(c, f"/repos/{owner}/{name}/issues", params={"state": "open", "per_page": 50})) or []
                snap.open_issues = len([i for i in issues if "pull_request" not in i])
                branches = (await _get(c, f"/repos/{owner}/{name}/branches", params={"per_page": 30})) or []
                snap.active_branches = [b["name"] for b in branches][:30]
                runs = await _get(c, f"/repos/{owner}/{name}/actions/runs", params={"per_page": 1, "branch": default_branch})
                if runs and runs.get("workflow_runs"):
                    r = runs["workflow_runs"][0]
                    snap.ci_status = r.get("conclusion") or r.get("status") or "unknown"
                else:
                    snap.ci_status = "unknown"
            except httpx.HTTPError as e:
                snap.freshness, snap.error = "unavailable", f"github unavailable: {e}"
        return snap


async def _get(c: httpx.AsyncClient, path: str, params: dict | None = None):
    r = await c.get(path, params=params)
    if r.status_code == 404:
        raise httpx.HTTPError(f"404 for {path}")
    r.raise_for_status()
    return r.json()


class FakeGitHubConnector:
    """Deterministic connector for tests/demo. `snapshots` keyed by "owner/name"."""

    def __init__(self, snapshots: dict[str, RepoSnapshot] | None = None, fail: bool = False):
        self.snapshots, self.fail, self.calls = snapshots or {}, fail, []

    async def snapshot(self, owner: str, name: str, default_branch: str = "main") -> RepoSnapshot:
        self.calls.append(f"{owner}/{name}")
        if self.fail:
            return RepoSnapshot(owner, name, default_branch, datetime.now(UTC).isoformat(), freshness="unavailable",
                                error="fake connector configured to fail")
        return self.snapshots.get(f"{owner}/{name}") or RepoSnapshot(
            owner, name, default_branch, datetime.now(UTC).isoformat(), commits_last_7d=3,
            last_commit_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
            last_commit_message="fake: latest commit", open_prs=1, open_issues=2, ci_status="success",
            active_branches=[default_branch, "feature/demo"], freshness="live")


def github_tool(connector: GitHubConnector, repo: GitHubRepoClient | None = None) -> ToolSpec:
    async def snapshot(owner: str, name: str, default_branch: str = "main") -> dict:
        return asdict(await connector.snapshot(owner, name, default_branch))

    async def create_issue(owner: str, name: str, title: str, body: str = "") -> dict:  # placeholder to prove L3 policy
        raise NotImplementedError("issue creation is not used yet")

    actions = {
        "snapshot": ToolAction("snapshot", ActionLevel.READ, "Normalized repository state", snapshot,
                               {"owner": "str", "name": "str", "default_branch": "str"}),
        "create_issue": ToolAction("create_issue", ActionLevel.EXECUTE_REVERSIBLE, "Open an issue", create_issue,
                                   {"owner": "str", "name": "str", "title": "str", "body": "str"}),
    }
    if repo is not None:
        async def open_fix_pr(owner: str, name: str, base_branch: str, branch: str, files: dict, message: str,
                              title: str, body: str) -> dict:
            commit = await repo.commit_files(owner, name, base_branch, branch, files, message)
            pr = await repo.open_pr(owner, name, branch, base_branch, title, body)
            return commit | {"pr_number": pr["number"], "pr_url": pr.get("url")}

        async def merge_pr(owner: str, name: str, number: int, method: str = "squash") -> dict:
            return await repo.merge_pr(owner, name, number, method)

        async def close_pr(owner: str, name: str, number: int, branch: str | None = None,
                           comment: str | None = None) -> dict:
            out = await repo.close_pr(owner, name, number, comment)
            if branch:
                out |= await repo.delete_branch(owner, name, branch)
            return out

        async def dependency_alerts(owner: str, name: str) -> dict:
            alerts = await repo.dependency_alerts(owner, name)
            return {"available": alerts is not None, "alerts": alerts or []}

        actions |= {
            # a new branch + PR never touches production: reversible (close PR, delete branch)
            "open_fix_pr": ToolAction("open_fix_pr", ActionLevel.EXECUTE_REVERSIBLE,
                                      "Commit a fix on a new branch and open a pull request", open_fix_pr),
            # merging ships to production through Vercel: ALWAYS requires explicit approval
            "merge_pr": ToolAction("merge_pr", ActionLevel.EXECUTE_SENSITIVE, "Merge a pull request", merge_pr),
            "close_pr": ToolAction("close_pr", ActionLevel.EXECUTE_REVERSIBLE, "Close a PR and delete its branch",
                                   close_pr),
            "dependency_alerts": ToolAction("dependency_alerts", ActionLevel.READ, "Open Dependabot alerts",
                                            dependency_alerts),
        }
    return ToolSpec("github", "GitHub repositories, commits, PRs, issues, CI", auth="token", actions=actions)
