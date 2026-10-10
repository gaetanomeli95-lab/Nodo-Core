"""A small, coherent simulated world for tests and DEMO mode: fake Vercel + fake GitHub + a prober that reads
site health from the fake deployments, plus a scripted "model" able to propose a code fix.

Everything is deterministic and offline. Pushing a branch to the fake GitHub creates a Vercel preview; merging a
PR creates a production deployment; a rollback changes which deployment the production domain serves. This lets
the whole night shift (detect -> rollback -> fix PR -> preview check -> approval -> merge) run end to end.
"""
from __future__ import annotations

import difflib
import hashlib
import itertools
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from nodo.db.models import Sensitivity
from nodo.night.probe import ProbeResult
from nodo.providers.base import ChatChunk, ChatMessage, ModelDescriptor, estimate_tokens
from nodo.tools.github_repo import FileChange, GitHubError
from nodo.tools.vercel import READY, Deployment, RuntimeLogs, VercelError, VercelProject

_counter = itertools.count(1)


def _sha(seed: str) -> str:
    return hashlib.sha1(f"{seed}-{next(_counter)}".encode()).hexdigest()


@dataclass
class SimHealth:
    status: int = 200
    body: str = "<html><head><title>Home</title></head><body>Benvenuto</body></html>"
    latency_ms: int = 180
    console_errors: list[str] = field(default_factory=list)
    pages: dict[str, int] = field(default_factory=dict)   # path -> status override


# ------------------------------------------------------------------------------------------------ Vercel
class FakeVercel:
    def __init__(self, token_ok: bool = True):
        self.projects: dict[str, VercelProject] = {}
        self.deployments: dict[str, Deployment] = {}
        self.health: dict[str, SimHealth] = {}
        self.build_logs: dict[str, list[str]] = {}
        self.runtime: dict[str, list[dict]] = {}
        self.calls: list[str] = []
        self.redeploy_state: dict[str, str] = {}        # project_id -> state of the next redeploy
        self.fail_rollback = False
        self.protected_previews = False
        self.bypass_secret = "demo-bypass"
        self.cert_days: dict[str, int] = {}
        self.token_ok = token_ok
        self.unhealthy_marker: str | None = None         # source text that makes a build unhealthy (see GitHub)
        self.auto_assign: dict[str, bool] = {}           # like Vercel: False after a rollback until a promote

    # ---------------------------------------------------------------- scenario helpers
    def add_project(self, name: str, owner: str | None, repo: str | None, branch: str = "main",
                    domains: list[str] | None = None) -> VercelProject:
        p = VercelProject(id=f"prj_{name.replace('-', '')[:16]}", name=name, framework="nextjs", repo_owner=owner,
                          repo_name=repo, production_branch=branch, domains=domains or [f"{name}.vercel.app"])
        self.projects[p.id] = p
        return p

    def deploy(self, project_id: str, *, state: str = READY, sha: str | None = None, message: str = "update",
               ref: str | None = None, target: str | None = "production", health: SimHealth | None = None,
               build_log: list[str] | None = None, runtime_errors: list[dict] | None = None,
               promote: bool = True, minutes_ago: int = 0) -> Deployment:
        p = self.projects[project_id]
        did = f"dpl_{_sha(project_id)[:10]}"
        d = Deployment(id=did, project_id=project_id, url=f"https://{p.name}-{did[4:11]}.vercel.app", state=state,
                       target=target, created_at=(datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(),
                       commit_sha=sha or _sha("c"), commit_message=message, commit_ref=ref or p.production_branch,
                       error_message="Command \"npm run build\" exited with 1" if state == "ERROR" else None)
        self.deployments[did] = d
        self.health[did] = health or SimHealth()
        self.build_logs[did] = build_log or (["Running build...", "Build Completed"] if state == READY else
                                             ["Running build...", "Error: build failed"])
        self.runtime[did] = runtime_errors or []
        if target == "production" and state == READY and promote and self.auto_assign.get(project_id, True):
            p.production_deployment_id = did
        return d

    def project_for_repo(self, owner: str, name: str) -> VercelProject | None:
        return next((p for p in self.projects.values() if p.repo_owner == owner and p.repo_name == name), None)

    def on_push(self, owner: str, name: str, branch: str, sha: str, message: str, files: dict[str, str]) -> None:
        p = self.project_for_repo(owner, name)
        if p:
            self.deploy(p.id, sha=sha, message=message, ref=branch, target=None, health=self._health_for(files))

    def on_merge(self, owner: str, name: str, sha: str, message: str, files: dict[str, str]) -> None:
        p = self.project_for_repo(owner, name)
        if p:
            self.deploy(p.id, sha=sha, message=message, ref=p.production_branch, health=self._health_for(files))

    def _health_for(self, files: dict[str, str]) -> SimHealth:
        if self.unhealthy_marker and any(self.unhealthy_marker in c for c in files.values()):
            return SimHealth(status=500, body="Internal Server Error")
        return SimHealth()

    # ---------------------------------------------------------------- connector API
    def _check(self):
        if not self.token_ok:
            raise VercelError("Vercel GET /v2/user -> HTTP 403: invalid token")

    async def whoami(self) -> dict:
        self._check()
        return {"username": "demo", "email": "demo@example.com", "team_id": None}

    async def list_projects(self) -> list[VercelProject]:
        self._check()
        self.calls.append("list_projects")
        return list(self.projects.values())

    async def get_project(self, project_id: str) -> VercelProject:
        self._check()
        if project_id not in self.projects:
            raise VercelError(f"project {project_id} not found")
        return self.projects[project_id]

    async def list_deployments(self, project_id: str, *, target: str | None = "production", limit: int = 10,
                               branch: str | None = None) -> list[Deployment]:
        self._check()
        deps = [d for d in self.deployments.values() if d.project_id == project_id
                and (target is None or d.target == target) and (branch is None or d.commit_ref == branch)]
        deps.sort(key=lambda d: d.created_at, reverse=True)
        return deps[:limit]

    async def get_deployment(self, deployment_id: str) -> Deployment:
        self._check()
        if deployment_id not in self.deployments:
            raise VercelError(f"deployment {deployment_id} not found")
        return self.deployments[deployment_id]

    async def build_log(self, deployment_id: str, limit: int = 300) -> list[str]:
        return self.build_logs.get(deployment_id, [])[-limit:]

    async def runtime_errors(self, project_id: str, deployment_id: str, seconds: float = 6.0) -> RuntimeLogs:
        errs = self.runtime.get(deployment_id, [])
        return RuntimeLogs(True, errors=list(errs), scanned=len(errs) + 40)

    async def rollback(self, project_id: str, deployment_id: str) -> dict:
        self.calls.append(f"rollback:{project_id}:{deployment_id}")
        if self.fail_rollback:
            raise VercelError("rollback refused (simulated)")
        self.projects[project_id].production_deployment_id = deployment_id
        self.auto_assign[project_id] = False
        return {"ok": True, "method": "rollback", "deployment_id": deployment_id}

    async def promote(self, project_id: str, deployment_id: str) -> dict:
        self.calls.append(f"promote:{project_id}:{deployment_id}")
        self.projects[project_id].production_deployment_id = deployment_id
        self.auto_assign[project_id] = True
        return {"ok": True, "method": "promote", "deployment_id": deployment_id}

    async def redeploy(self, project_name: str, deployment_id: str) -> Deployment:
        self.calls.append(f"redeploy:{deployment_id}")
        old = self.deployments[deployment_id]
        state = self.redeploy_state.get(old.project_id, READY)
        return self.deploy(old.project_id, state=state, sha=old.commit_sha, message=old.commit_message or "redeploy",
                           ref=old.commit_ref, health=SimHealth())


# ------------------------------------------------------------------------------------------------ GitHub
class FakeGitHubRepo:
    def __init__(self, vercel: FakeVercel | None = None, token_ok: bool = True):
        self.vercel = vercel
        self.repos: dict[str, dict] = {}
        self.prs: dict[tuple[str, int], dict] = {}
        self.alerts: dict[str, list[dict] | None] = {}
        self.calls: list[str] = []
        self.token_ok = token_ok

    def add_repo(self, owner: str, name: str, files: dict[str, str], branch: str = "main",
                 message: str = "initial commit") -> str:
        sha = _sha(f"{owner}/{name}")
        self.repos[f"{owner}/{name}"] = {"default": branch, "branches": {branch: sha},
                                         "commits": {sha: {"files": dict(files), "message": message, "parent": None,
                                                           "date": datetime.now(UTC).isoformat()}}}
        return sha

    def commit(self, owner: str, name: str, branch: str, changes: dict[str, str], message: str) -> str:
        """Simulate a developer pushing to a branch (returns the new sha)."""
        r = self.repos[f"{owner}/{name}"]
        parent = r["branches"][branch]
        files = dict(r["commits"][parent]["files"]) | changes
        sha = _sha("dev")
        r["commits"][sha] = {"files": files, "message": message, "parent": parent, "date": datetime.now(UTC).isoformat()}
        r["branches"][branch] = sha
        return sha

    def _repo(self, owner: str, name: str) -> dict:
        if not self.token_ok:
            raise GitHubError("GitHub GET /user -> HTTP 401: Bad credentials")
        key = f"{owner}/{name}"
        if key not in self.repos:
            raise GitHubError(f"GitHub repo {key} -> HTTP 404")
        return self.repos[key]

    def _files(self, r: dict, ref: str) -> dict[str, str]:
        sha = r["branches"].get(ref, ref)
        if sha not in r["commits"]:
            raise GitHubError(f"ref {ref} -> HTTP 404")
        return r["commits"][sha]["files"]

    async def whoami(self) -> dict:
        if not self.token_ok:
            raise GitHubError("GitHub GET /user -> HTTP 401: Bad credentials")
        return {"login": "demo", "name": "Demo"}

    async def tree(self, owner: str, name: str, ref: str) -> list[str]:
        return sorted(self._files(self._repo(owner, name), ref))

    async def get_file(self, owner: str, name: str, path: str, ref: str) -> str | None:
        return self._files(self._repo(owner, name), ref).get(path)

    async def compare(self, owner: str, name: str, base: str, head: str) -> list[FileChange]:
        r = self._repo(owner, name)
        a, b = self._files(r, base), self._files(r, head)
        out = []
        for path in sorted(set(a) | set(b)):
            if a.get(path) == b.get(path):
                continue
            status = "added" if path not in a else "removed" if path not in b else "modified"
            patch = "".join(difflib.unified_diff((a.get(path) or "").splitlines(True), (b.get(path) or "").splitlines(True)))
            out.append(FileChange(path, status, patch))
        return out

    async def recent_commits(self, owner: str, name: str, branch: str, n: int = 5) -> list[dict]:
        r = self._repo(owner, name)
        sha, out = r["branches"].get(branch), []
        while sha and len(out) < n:
            c = r["commits"][sha]
            out.append({"sha": sha, "message": c["message"], "date": c["date"]})
            sha = c["parent"]
        return out

    async def commit_files(self, owner: str, name: str, base_branch: str, new_branch: str,
                           files: dict[str, str], message: str) -> dict:
        r = self._repo(owner, name)
        if new_branch in r["branches"]:
            raise GitHubError(f"Reference already exists: {new_branch} -> HTTP 422")
        base_sha = r["branches"][base_branch]
        r["branches"][new_branch] = base_sha
        sha = self.commit(owner, name, new_branch, files, message)
        self.calls.append(f"commit:{new_branch}")
        if self.vercel:
            self.vercel.on_push(owner, name, new_branch, sha, message, r["commits"][sha]["files"])
        return {"branch": new_branch, "sha": sha, "base_sha": base_sha}

    async def open_pr(self, owner: str, name: str, head: str, base: str, title: str, body: str) -> dict:
        self._repo(owner, name)
        number = 100 + len(self.prs) + 1
        self.prs[(f"{owner}/{name}", number)] = {"head": head, "base": base, "title": title, "body": body,
                                                 "state": "open", "merged": False}
        self.calls.append(f"open_pr:{number}")
        return {"number": number, "url": f"https://github.com/{owner}/{name}/pull/{number}"}

    async def pr_state(self, owner: str, name: str, number: int) -> dict:
        pr = self.prs[(f"{owner}/{name}", number)]
        return {"state": pr["state"], "merged": pr["merged"], "mergeable": True, "head_sha": None}

    async def merge_pr(self, owner: str, name: str, number: int, method: str = "squash") -> dict:
        r = self._repo(owner, name)
        pr = self.prs[(f"{owner}/{name}", number)]
        if pr["state"] != "open":
            raise GitHubError("Pull Request is not mergeable -> HTTP 405")
        head_files = self._files(r, pr["head"])
        sha = self.commit(owner, name, pr["base"], head_files, f"{pr['title']} (#{number})")
        pr.update(state="closed", merged=True)
        self.calls.append(f"merge:{number}")
        if self.vercel:
            self.vercel.on_merge(owner, name, sha, pr["title"], head_files)
        return {"merged": True, "sha": sha, "message": "Pull Request successfully merged"}

    async def close_pr(self, owner: str, name: str, number: int, comment: str | None = None) -> dict:
        self.prs[(f"{owner}/{name}", number)]["state"] = "closed"
        self.calls.append(f"close:{number}")
        return {"state": "closed"}

    async def delete_branch(self, owner: str, name: str, branch: str) -> dict:
        self._repo(owner, name)["branches"].pop(branch, None)
        return {"deleted": branch}

    async def create_issue(self, owner: str, name: str, title: str, body: str = "") -> dict:
        self._repo(owner, name)
        self.calls.append(f"issue:{title}")
        return {"number": 1, "url": f"https://github.com/{owner}/{name}/issues/1"}

    async def dependency_alerts(self, owner: str, name: str) -> list[dict] | None:
        self._repo(owner, name)
        return self.alerts.get(f"{owner}/{name}", [])


# ------------------------------------------------------------------------------------------------ prober
class SimulatedProber:
    """Answers probes from the fake Vercel state: domain -> production deployment -> its SimHealth."""

    def __init__(self, vercel: FakeVercel):
        self.vercel = vercel
        self.calls: list[str] = []

    def _resolve(self, host: str) -> Deployment | None:
        for d in self.vercel.deployments.values():
            if urlparse(d.url).hostname == host:
                return d
        for p in self.vercel.projects.values():
            if host in p.domains and p.production_deployment_id:
                return self.vercel.deployments[p.production_deployment_id]
        return None

    async def probe(self, url: str, *, expect: str | None = None, headers: dict | None = None,
                    browser: bool = False) -> ProbeResult:
        self.calls.append(url)
        u = urlparse(url)
        d = self._resolve(u.hostname or "")
        if d is None:
            return ProbeResult(url=url, ok=False, error="irraggiungibile (ConnectError)")
        if d.target is None and self.vercel.protected_previews and \
                (headers or {}).get("x-vercel-protection-bypass") != self.vercel.bypass_secret:
            return ProbeResult(url=url, ok=False, status=401, protected=True)
        h = self.vercel.health.get(d.id, SimHealth())
        status = h.pages.get(u.path or "/", h.status)
        found = None if expect is None else expect.lower() in h.body.lower()
        title = m.group(1) if (m := re.search(r"<title>(.*?)</title>", h.body)) else None
        return ProbeResult(url=url, ok=status < 400 and found is not False, status=status, latency_ms=h.latency_ms,
                           title=title, expect=expect, expect_found=found,
                           console_errors=list(h.console_errors) if browser else [], size=len(h.body),
                           method="browser" if browser else "http")

    async def cert_days_left(self, url: str) -> int | None:
        return self.vercel.cert_days.get(urlparse(url).hostname or "", 80)

    async def close(self) -> None:
        return None


# ------------------------------------------------------------------------------------------------ model
class SimulatedModelProvider:
    """Offline 'model' for tests/DEMO. Code-fix prompts: applies scripted (search -> replace) rules to the files in
    the prompt and answers with the JSON the fix agent expects. Draft prompts: returns a templated draft."""

    name = "simulated"

    def __init__(self, rules: list[tuple[str, str, str]] | None = None, coding: int = 5):
        # rules: (search, replace, diagnosis)
        self.rules = rules or []
        self.coding = coding
        self.calls: list[str] = []

    def models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor(provider=self.name, model="sim-coder", reasoning=5, coding=self.coding, is_local=True,
                                tier="local", latency_class="low", max_sensitivity=Sensitivity.HIGHLY_SENSITIVE,
                                context_window=200_000)]

    async def available(self) -> bool:
        return True

    def _answer(self, prompt: str) -> str:
        if "[NODO_FIX_REQUEST]" in prompt:
            self.calls.append("fix")
            files = dict(re.findall(r"=== FILE: (.+?) ===\n(.*?)\n=== END FILE ===", prompt, re.DOTALL))
            for search, replace, diagnosis in self.rules:
                for path, content in files.items():
                    if search in content:
                        return json.dumps({"diagnosis": diagnosis, "confidence": "high",
                                           "title": f"fix: {path.rsplit('/', 1)[-1]}",
                                           "files": [{"path": path, "content": content.replace(search, replace)}],
                                           "test_hint": "Apri la pagina e verifica che si carichi."}, ensure_ascii=False)
            return json.dumps({"diagnosis": "Non ho trovato una causa certa nei file disponibili.",
                               "confidence": "low", "title": "", "files": []})
        if "[NODO_CHANGE_REQUEST]" in prompt:
            # requested change: "... 'old text' in 'new text'" -> replace it in the files that contain it
            self.calls.append("change")
            files = dict(re.findall(r"=== FILE: (.+?) ===\n(.*?)\n=== END FILE ===", prompt, re.DOTALL))
            req = m.group(1) if (m := re.search(r"RICHIESTA DI GAETANO: (.+)", prompt)) else ""
            q = re.findall(r"[\"'«“]([^\"'«»“”]+)[\"'»”]", req)
            if len(q) >= 2:
                for old, new in ((q[0], q[1]), (q[1], q[0])):   # "'A' in 'B'" or "'B' al posto di 'A'"
                    for path, content in files.items():
                        if old in content and new not in content:
                            return json.dumps({"diagnosis": f"Ho sostituito «{old}» con «{new}» in {path}.",
                                               "confidence": "high", "title": f"feat: {new}",
                                               "files": [{"path": path, "content": content.replace(old, new)}],
                                               "test_hint": "Apri la home."}, ensure_ascii=False)
            return json.dumps({"diagnosis": "Non ho capito cosa cambiare: mi dici il testo esatto?",
                               "confidence": "low", "title": "", "files": []})
        if "[NODO_DRAFT_REQUEST]" in prompt:
            self.calls.append("draft")
            title = m.group(1) if (m := re.search(r"Titolo: (.+)", prompt)) else "contenuto"
            return (f"Bozza per \"{title}\".\n\nApertura: una domanda diretta al pubblico.\n"
                    "Sviluppo: tre punti concreti, un esempio reale.\nChiusura: invito all'azione semplice.")
        m = re.search(r"\[GROUNDED_SUMMARY\]\n?(.*?)\n?\[/GROUNDED_SUMMARY\]", prompt, re.DOTALL)
        return m.group(1).strip() if m else "Ok."

    async def stream(self, model: str, messages: list[ChatMessage], *, temperature: float = 0.3,
                     max_tokens: int = 800) -> AsyncIterator[ChatChunk]:
        prompt = "\n".join(m.content for m in messages)
        text = self._answer(prompt)
        for i in range(0, len(text), 80):
            yield ChatChunk(delta=text[i:i + 80])
        yield ChatChunk(done=True, tokens_in=estimate_tokens(prompt), tokens_out=estimate_tokens(text))


# ------------------------------------------------------------------------------------------------ demo world
DEMO_OWNER = "studio-nodo-demo"
DEMO_BUG = "data.items.map("
DEMO_FIX = "(data?.items ?? []).map("

_CONTACT_PAGE_GOOD = """import { getServices } from "@/lib/services";

export default async function Contatti() {
  const data = await getServices();
  return (
    <main>
      <h1>Contattaci</h1>
      <ul>{(data?.items ?? []).map((s) => <li key={s.id}>{s.name}</li>)}</ul>
    </main>
  );
}
"""


@dataclass
class DemoWorld:
    vercel: FakeVercel
    github: FakeGitHubRepo
    prober: SimulatedProber
    model: SimulatedModelProvider
    projects: dict[str, VercelProject]

    def break_things(self) -> None:
        """What 'happens during the night' in the demo: a bad deploy, a flaky build, a security alert."""
        pb = self.projects["pbcare-platform"]
        bad_page = _CONTACT_PAGE_GOOD.replace(DEMO_FIX, DEMO_BUG)
        sha = self.github.commit(DEMO_OWNER, "pbcare-platform", "main", {"src/app/contatti/page.tsx": bad_page},
                                 "feat: nuovo elenco servizi nella pagina contatti")
        self.vercel.deploy(pb.id, sha=sha, message="feat: nuovo elenco servizi nella pagina contatti",
                           health=SimHealth(status=500, body="Internal Server Error"),
                           runtime_errors=[{"message": "TypeError: Cannot read properties of undefined (reading 'map')"
                                                       "\n    at Contatti (src/app/contatti/page.tsx:9:30)",
                                            "path": "/contatti", "status": 500, "at": datetime.now(UTC).isoformat()}] * 7)
        pr = self.projects["prosperya-crm"]
        self.vercel.deploy(pr.id, state="ERROR", sha=_sha("p"), message="chore: aggiornamento dipendenze",
                           build_log=["Installing dependencies...", "npm ERR! code ETIMEDOUT",
                                      "npm ERR! network request to https://registry.npmjs.org/next failed, reason: ETIMEDOUT",
                                      "Error: Command \"npm install\" exited with 1"])
        self.github.alerts[f"{DEMO_OWNER}/cf-materassi-sito"] = [
            {"severity": "high", "package": "next", "summary": "Bypass dell'autorizzazione nel middleware", "url": None},
            {"severity": "moderate", "package": "postcss", "summary": "Parsing errato dei commenti", "url": None}]


def build_demo_world() -> DemoWorld:
    vercel = FakeVercel()
    github = FakeGitHubRepo(vercel)
    vercel.unhealthy_marker = DEMO_BUG
    projects: dict[str, VercelProject] = {}
    specs = [("pbcare-platform", ["app.pbcare-demo.it"], {"src/app/contatti/page.tsx": _CONTACT_PAGE_GOOD,
                                                         "src/lib/services.ts": "export async function getServices() {\n  return { items: [] };\n}\n",
                                                         "package.json": "{\n  \"name\": \"pbcare-platform\"\n}\n"}),
             ("prosperya-crm", ["crm.prosperya-demo.it"], {"src/app/page.tsx": "export default function Home() { return <h1>CRM</h1>; }\n",
                                                          "package.json": "{\n  \"name\": \"prosperya-crm\"\n}\n"}),
             ("cf-materassi-sito", ["cfmaterassi-demo.it"], {"src/app/page.tsx": "export default function Home() { return <h1>CF</h1>; }\n",
                                                            "package.json": "{\n  \"name\": \"cf-materassi-sito\"\n}\n"})]
    for name, domains, files in specs:
        p = vercel.add_project(name, DEMO_OWNER, name, domains=domains)
        sha = github.add_repo(DEMO_OWNER, name, files)
        vercel.deploy(p.id, sha=sha, message="initial commit", minutes_ago=24 * 60)
        projects[name] = p
    model = SimulatedModelProvider(rules=[(DEMO_BUG, DEMO_FIX,
                                           "La pagina /contatti va in errore 500: il codice usa data.items.map(...) ma "
                                           "quando il servizio non restituisce elementi `items` è undefined. "
                                           "Correzione: uso un array vuoto come valore di riserva.")])
    return DemoWorld(vercel, github, SimulatedProber(vercel), model, projects)
