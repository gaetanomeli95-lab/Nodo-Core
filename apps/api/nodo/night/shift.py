"""The night shift: watch every site, notice what broke, fix what is safe to fix, prepare what needs you.

One `NightShift.run()` = one pass:
  1. follow up on yesterday's fix proposals (preview ready? PR merged/closed elsewhere?)
  2. (full runs) import new Vercel projects as sites
  3. observe every site concurrently — network only, no database writes
  4. evaluate sequentially — checks, incidents (deduplicated, self-resolving), "last known good" tracking
  5. respond — rollback / redeploy (reversible, auto-approved by your config) and fix PRs (merge needs you)
  6. (full runs) content drafts
Every external action goes through the ToolExecutor (permission policy + ToolExecution record) and every
outcome is written as a NightAction, so the morning briefing can tell you exactly what happened.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nodo.config import Settings
from nodo.core.events import EventBus
from nodo.core.permissions import PermissionPolicy
from nodo.core.telemetry import Telemetry
from nodo.db.models import (
    FixProposal,
    Incident,
    InferenceRun,
    NightAction,
    NightRun,
    Project,
    Repository,
    Site,
    SiteCheck,
)
from nodo.night.fixer import Evidence, FixAgent
from nodo.night.notify import Notifier, NullNotifier
from nodo.night.probe import Prober, ProbeResult
from nodo.providers.base import ModelProvider
from nodo.providers.router import ModelRouter
from nodo.tools.base import ToolExecutor
from nodo.tools.github import GitHubConnector, github_tool
from nodo.tools.github_repo import GitHubRepoClient
from nodo.tools.vercel import READY, Deployment, RuntimeLogs, VercelConnector, VercelProject, vercel_tool

log = logging.getLogger("nodo.night")

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
STATUS_RANK = {"skipped": -1, "error": 0, "ok": 1, "warning": 2, "critical": 3}
TRANSIENT_BUILD = re.compile(r"ETIMEDOUT|ECONNRESET|ENOTFOUND|EAI_AGAIN|socket hang up|network (request|timeout)|"
                             r"rate limit|429 Too Many|50[234] (Bad Gateway|Service|Gateway)|Internal (server )?error|"
                             r"timed out|registry\.npmjs\.org.*failed|Unexpected end of JSON input|"
                             r"Build container .* (failed|exited)|out of memory|SIGKILL", re.IGNORECASE)
ERROR_LINE = re.compile(r"error|ERR!|failed|cannot|unexpected|not found|exception|TypeError|ReferenceError",
                        re.IGNORECASE)
KIND_IT = {"site_down": "sito non raggiungibile", "page_broken": "pagina rotta", "deploy_failed": "build fallita",
           "runtime_errors": "errori del server", "js_errors": "errori JavaScript", "slow": "sito lento",
           "ssl_expiring": "certificato in scadenza", "vulnerabilities": "dipendenze vulnerabili",
           "ci_failing": "CI in errore"}


@dataclass
class NightDeps:
    """External world the night shift talks to. Any of them may be missing; the shift degrades honestly."""
    prober: Prober
    vercel: VercelConnector | None = None
    repo: GitHubRepoClient | None = None
    github: GitHubConnector | None = None
    providers: list[ModelProvider] = field(default_factory=list)
    notifier: Notifier = field(default_factory=NullNotifier)
    verify_delay: float = 20.0     # seconds between a rollback and its verification
    retry_delay: float = 5.0       # seconds before re-probing a failing page (avoid false alarms)
    poll_interval: float = 15.0    # seconds between deployment/preview polls


@dataclass
class Signal:
    kind: str
    severity: str
    key: str
    title: str
    details: dict = field(default_factory=dict)


@dataclass
class CheckOutcome:
    check: str
    status: str
    summary: str
    details: dict = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    owns: tuple[str, ...] = ()
    duration_ms: int = 0


@dataclass
class Observation:
    site_id: str
    project: VercelProject | None = None
    recent: list[Deployment] = field(default_factory=list)       # production-target deployments, newest first
    latest: Deployment | None = None
    production: Deployment | None = None                         # what the production domain serves now
    build_log: list[str] = field(default_factory=list)
    runtime: RuntimeLogs | None = None
    probes: list[tuple[dict, ProbeResult, int]] = field(default_factory=list)  # (page, result, attempts)
    cert_days: int | None = None
    alerts: list[dict] | None = None
    alerts_checked: bool = False
    ci_status: str | None = None
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


def _dt(v: str | datetime | None) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def _clock(v: str | datetime | None, tz=None) -> str:
    d = _dt(v)
    if not d:
        return "?"
    return d.astimezone(tz).strftime("%H:%M") if tz else d.strftime("%H:%M")


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + ("/" + path.lstrip("/") if path and path != "/" else "/")


def site_pages(site: Site) -> list[dict]:
    pages = [{"path": "/", "expect": None, "name": "home"}]
    for p in site.key_pages or []:
        path = p.get("path") if isinstance(p, dict) else str(p)
        if not path:
            continue
        if path == "/":
            pages[0]["expect"] = p.get("expect") if isinstance(p, dict) else None
            continue
        pages.append({"path": path, "expect": p.get("expect") if isinstance(p, dict) else None,
                      "name": (p.get("name") if isinstance(p, dict) else None) or path})
    return pages


class NightShift:
    def __init__(self, session: Session, settings: Settings, organization_id: str, deps: NightDeps,
                 events: EventBus | None = None, tz=None):
        self.s, self.settings, self.org, self.deps = session, settings, organization_id, deps
        self.events = events or EventBus(session)
        self.telemetry = Telemetry(session)
        self.tz = tz
        self.request_id = str(uuid.uuid4())
        self.base_policy = self._policy({})
        self.tools = ToolExecutor(session, self.events, self.base_policy, organization_id)
        if deps.vercel:
            self.tools.register(vercel_tool(deps.vercel))
        if deps.github or deps.repo:
            self.tools.register(github_tool(deps.github, deps.repo))
        allow_paid = settings.allow_paid_in_free_mode or settings.night_fix_allow_paid
        self.fix_router = ModelRouter(deps.providers, settings.mode, allow_paid, recorder=self.telemetry.record_inference,
                                  cooldown_minutes=settings.provider_cooldown_minutes)
        self.router = ModelRouter(deps.providers, settings.mode, settings.allow_paid_in_free_mode,
                                  recorder=self.telemetry.record_inference,
                                  cooldown_minutes=settings.provider_cooldown_minutes)
        self.run: NightRun | None = None
        self.last_review = None
        self._fixes_this_run = 0

    # ------------------------------------------------------------------ policy
    def _site_flag(self, site_policy: dict, key: str, default: bool) -> bool:
        v = (site_policy or {}).get(key)
        return default if v is None else bool(v)

    def _policy(self, site_policy: dict) -> PermissionPolicy:
        st = self.settings
        auto = {"github.close_pr"}
        if self._site_flag(site_policy, "rollback", st.night_auto_rollback):
            auto.add("vercel.rollback")
        if self._site_flag(site_policy, "redeploy", st.night_auto_redeploy):
            auto.add("vercel.redeploy")
        if self._site_flag(site_policy, "fix_prs", st.night_fix_prs):
            auto.add("github.open_fix_pr")
        # merge_pr is level 4 and is NEVER in this set: it always waits for you
        return PermissionPolicy(auto_approve_actions=auto)

    # ------------------------------------------------------------------ entry point
    async def run_pass(self, kind: str = "manual", trigger: str = "manual", site_ids: list[str] | None = None) -> NightRun:
        full = kind in ("night", "manual")
        run = NightRun(organization_id=self.org, request_id=self.request_id, kind=kind, trigger=trigger, summary={})
        self.s.add(run)
        self.s.flush()
        self.run = run
        self.events.emit("night.run.started", {"run_id": run.id, "kind": kind, "trigger": trigger},
                         request_id=self.request_id, organization_id=self.org)
        self.s.commit()
        counts = {"sites": 0, "checks": 0, "ok": 0, "warning": 0, "critical": 0, "unknown": 0, "incidents_new": 0,
                  "incidents_resolved": 0, "actions": {}, "fixes": 0, "drafts": 0}
        try:
            if full and self.deps.vercel and self.settings.night_auto_discover:
                try:
                    await self.discover()
                except Exception as e:
                    counts.setdefault("errors", []).append(f"importazione progetti Vercel: {e}")
            await self.follow_up()
            q = select(Site).where(Site.organization_id == self.org, Site.active.is_(True)).order_by(Site.name)
            sites = [s for s in self.s.scalars(q) if not site_ids or s.id in site_ids]
            sem = asyncio.Semaphore(4)

            async def observe(site: Site) -> Observation:
                async with sem:
                    return await self.observe(site, full)

            observations = await asyncio.gather(*(observe(s) for s in sites))
            for site, obs in zip(sites, observations, strict=True):
                stats = await self.evaluate(site, obs, full)
                counts["sites"] += 1
                counts["checks"] += stats["checks"]
                counts[site.status if site.status in counts else "unknown"] += 1
                counts["incidents_new"] += stats["new"]
                counts["incidents_resolved"] += stats["resolved"]
                self.s.commit()
            if full and self.settings.night_drafts and kind == "night":
                from nodo.night.drafts import prepare_drafts
                counts["drafts"] = await prepare_drafts(self, run)
            counts["fixes"] = self._fixes_this_run
            counts["actions"] = dict(self.s.execute(
                select(NightAction.action, func.count()).where(NightAction.run_id == run.id)
                .group_by(NightAction.action)).all())
            counts["cost_usd"] = round(self.spent(run_ids=[run.id]), 4)
            run.status = "done"
        except Exception as e:  # a broken pass must never take the scheduler down
            log.exception("night run failed")
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"
        finally:
            run.summary = counts
            run.finished_at = datetime.now(UTC)
            self.events.emit("night.run.finished", {"run_id": run.id, "status": run.status, **{
                k: v for k, v in counts.items() if isinstance(v, int)}}, request_id=self.request_id,
                organization_id=self.org)
            self.s.commit()
            await self.deps.prober.close()
        return run

    # ------------------------------------------------------------------ discovery
    async def discover(self) -> list[Site]:
        """Import Vercel projects as sites (idempotent). Links a site to a NODO project when names match."""
        assert self.deps.vercel
        created: list[Site] = []
        projects = await self.deps.vercel.list_projects()
        existing = {s.vercel_project_id: s for s in self.s.scalars(select(Site).where(Site.organization_id == self.org))
                    if s.vercel_project_id}
        for p in projects:
            site = existing.get(p.id)
            if site is None:
                site = Site(organization_id=self.org, name=p.name, url=p.primary_url, vercel_project_id=p.id,
                            vercel_project_name=p.name, repo_owner=p.repo_owner, repo_name=p.repo_name,
                            default_branch=p.production_branch, key_pages=[], policy={}, aliases=[])
                site.project_id = self._match_project(p)
                self.s.add(site)
                created.append(site)
                self.events.emit("night.site.discovered", {"name": p.name, "url": p.primary_url},
                                 request_id=self.request_id, organization_id=self.org)
            else:  # keep repo link and branch in sync; never override a URL you edited by hand
                site.vercel_project_name = p.name
                site.repo_owner = site.repo_owner or p.repo_owner
                site.repo_name = site.repo_name or p.repo_name
                site.default_branch = p.production_branch or site.default_branch
        self.s.flush()
        return created

    def _match_project(self, p: VercelProject) -> str | None:
        if p.repo_owner and p.repo_name:
            repo = self.s.scalars(select(Repository).where(Repository.owner == p.repo_owner,
                                                           Repository.name == p.repo_name)).first()
            if repo:
                return repo.project_id
        norm = re.sub(r"[^a-z0-9]", "", p.name.lower())
        for proj in self.s.scalars(select(Project).where(Project.organization_id == self.org)):
            names = [proj.name, proj.slug, *(proj.aliases or [])]
            if any(re.sub(r"[^a-z0-9]", "", n.lower()) == norm for n in names if n):
                return proj.id
        return None

    # ------------------------------------------------------------------ observe (network only)
    async def observe(self, site: Site, full: bool) -> Observation:
        t0 = asyncio.get_running_loop().time()
        obs = Observation(site.id)
        v = self.deps.vercel
        if v and site.vercel_project_id:
            try:
                obs.project = await v.get_project(site.vercel_project_id)
                obs.recent = await v.list_deployments(site.vercel_project_id, target="production", limit=8)
                obs.latest = obs.recent[0] if obs.recent else None
                prod_id = obs.project.production_deployment_id
                obs.production = next((d for d in obs.recent if d.id == prod_id), None)
                if obs.production is None and prod_id:
                    obs.production = await v.get_deployment(prod_id)
                if obs.production is None:
                    obs.production = next((d for d in obs.recent if d.state == READY), None)
                if obs.latest and obs.latest.state == "ERROR":
                    obs.build_log = (await v.build_log(obs.latest.id, 300))[-80:]
                if full and obs.production:
                    obs.runtime = await v.runtime_errors(site.vercel_project_id, obs.production.id, 6.0)
            except Exception as e:
                obs.errors.append(f"Vercel: {str(e)[:200]}")
        browser = full and self.settings.night_browser_checks
        for page in site_pages(site):
            url = _join_url(site.url, page["path"])
            r = await self.deps.prober.probe(url, expect=page.get("expect"), browser=browser)
            attempts = 1
            if not r.ok and not r.protected:  # confirm before calling it a failure
                await asyncio.sleep(self.deps.retry_delay)
                r = await self.deps.prober.probe(url, expect=page.get("expect"), browser=browser)
                attempts = 2
            obs.probes.append((page, r, attempts))
        if full:
            obs.cert_days = await self.deps.prober.cert_days_left(site.url)
            if self.deps.repo and site.repo_owner and site.repo_name:
                try:
                    obs.alerts = await self.deps.repo.dependency_alerts(site.repo_owner, site.repo_name)
                    obs.alerts_checked = True
                except Exception as e:
                    obs.errors.append(f"GitHub alert: {str(e)[:160]}")
            if self.deps.github and site.repo_owner and site.repo_name:
                try:
                    snap = await self.deps.github.snapshot(site.repo_owner, site.repo_name, site.default_branch)
                    obs.ci_status = snap.ci_status if snap.freshness == "live" else None
                except Exception:
                    pass
        obs.duration_ms = int((asyncio.get_running_loop().time() - t0) * 1000)
        return obs

    # ------------------------------------------------------------------ checks (pure)
    def checks(self, site: Site, obs: Observation, full: bool) -> list[CheckOutcome]:
        out = [self._check_deploy(site, obs), self._check_uptime(obs), self._check_pages(obs), self._check_speed(obs)]
        if full:
            out += [self._check_runtime(obs), self._check_ssl(site, obs), self._check_security(obs),
                    self._check_ci(obs)]
        return out

    def _check_deploy(self, site: Site, obs: Observation) -> CheckOutcome:
        if not site.vercel_project_id or not self.deps.vercel:
            return CheckOutcome("deploy", "skipped", "nessun progetto Vercel collegato")
        if obs.errors and obs.project is None:
            return CheckOutcome("deploy", "error", obs.errors[0])
        latest, prod = obs.latest, obs.production
        if latest is None:
            return CheckOutcome("deploy", "ok", "nessun deploy in produzione", owns=("deploy_failed",))
        if latest.state == "ERROR" and (prod is None or (_dt(latest.created_at) or datetime.min.replace(tzinfo=UTC))
                                        > (_dt(prod.created_at) or datetime.min.replace(tzinfo=UTC))):
            errs = [ln for ln in obs.build_log if ERROR_LINE.search(ln)][-15:] or obs.build_log[-15:]
            text = "\n".join(obs.build_log[-80:])
            transient = bool(TRANSIENT_BUILD.search(text))
            msg = latest.commit_message or "ultimo commit"
            sig = Signal("deploy_failed", "warning", "build",
                         f"Build fallita: «{msg}» (in produzione resta la versione precedente)",
                         {"deployment_id": latest.id, "commit_sha": latest.commit_sha, "commit_message": msg,
                          "error_lines": errs, "transient": transient, "error_message": latest.error_message})
            return CheckOutcome("deploy", "warning", sig.title, {"deployment_id": latest.id, "transient": transient},
                                [sig], ("deploy_failed",))
        if latest.state in ("BUILDING", "QUEUED", "INITIALIZING"):
            return CheckOutcome("deploy", "ok", f"deploy in corso: «{latest.commit_message or latest.id}»",
                                {"deployment_id": latest.id}, owns=())
        if prod:
            return CheckOutcome("deploy", "ok", f"in produzione «{prod.commit_message or prod.id}» "
                                                f"(deploy delle {_clock(prod.created_at, self.tz)})",
                                {"deployment_id": prod.id, "commit_sha": prod.commit_sha}, owns=("deploy_failed",))
        return CheckOutcome("deploy", "ok", "deploy regolare", owns=("deploy_failed",))

    def _check_uptime(self, obs: Observation) -> CheckOutcome:
        if not obs.probes:
            return CheckOutcome("uptime", "skipped", "nessuna pagina da controllare")
        page, r, attempts = obs.probes[0]
        det = {"url": r.url, "status": r.status, "latency_ms": r.latency_ms, "attempts": attempts, "error": r.error,
               "title": r.title}
        if r.protected:
            return CheckOutcome("uptime", "warning", "la home è protetta da Vercel Authentication: non posso vederla",
                                det, owns=("site_down",))
        if r.ok:
            return CheckOutcome("uptime", "ok", f"online ({r.status}, {r.latency_ms} ms)", det, owns=("site_down",))
        what = r.error or (f"HTTP {r.status}" if r.status else "nessuna risposta")
        if r.expect_found is False and r.status and r.status < 400:
            what = f"manca il testo atteso «{r.expect}»"
        title = f"Il sito non funziona: la home risponde {what}"
        sig = Signal("site_down", "critical", "home", title, det | {"path": "/"})
        return CheckOutcome("uptime", "critical", title, det, [sig], ("site_down",))

    def _check_pages(self, obs: Observation) -> CheckOutcome:
        if obs.probes and obs.probes[0][1].down:  # whole site down: other pages failing is the same problem
            return CheckOutcome("pages", "skipped", "home non disponibile: pagine non valutate")
        signals: list[Signal] = []
        lines: list[str] = []
        for i, (page, r, _) in enumerate(obs.probes):
            if r.console_errors:
                signals.append(Signal("js_errors", "warning", page["path"],
                                      f"Errori JavaScript su {page['path']} ({len(r.console_errors)})",
                                      {"path": page["path"], "errors": r.console_errors[:5], "url": r.url}))
            if i == 0 or r.protected:
                continue
            if not r.ok:
                sev = "critical" if r.down else "warning"
                what = r.error or (f"manca il testo «{r.expect}»" if r.expect_found is False else f"HTTP {r.status}")
                signals.append(Signal("page_broken", sev, page["path"], f"Pagina {page['path']} rotta: {what}",
                                      {"path": page["path"], "status": r.status, "url": r.url, "error": r.error}))
            lines.append(f"{page['path']}: {r.short()}")
        if not signals:
            n = max(0, len(obs.probes) - 1)
            return CheckOutcome("pages", "ok" if n else "skipped",
                                f"{n} pagine chiave regolari" if n else "nessuna pagina chiave configurata",
                                {"pages": lines}, owns=("page_broken", "js_errors"))
        worst = max(signals, key=lambda s: SEVERITY_RANK[s.severity])
        return CheckOutcome("pages", worst.severity, "; ".join(s.title for s in signals[:3]), {"pages": lines}, signals,
                            ("page_broken", "js_errors"))

    def _check_speed(self, obs: Observation) -> CheckOutcome:
        if not obs.probes or not obs.probes[0][1].ok:
            return CheckOutcome("speed", "skipped", "home non disponibile")
        r = obs.probes[0][1]
        if r.latency_ms > self.settings.night_slow_ms:
            sig = Signal("slow", "warning", "home", f"Home lenta: {r.latency_ms / 1000:.1f} s",
                         {"latency_ms": r.latency_ms})
            return CheckOutcome("speed", "warning", sig.title, {"latency_ms": r.latency_ms}, [sig], ("slow",))
        return CheckOutcome("speed", "ok", f"{r.latency_ms} ms", {"latency_ms": r.latency_ms}, owns=("slow",))

    def _check_runtime(self, obs: Observation) -> CheckOutcome:
        rt = obs.runtime
        if rt is None:
            return CheckOutcome("runtime", "skipped", "log non disponibili")
        if not rt.available:
            return CheckOutcome("runtime", "skipped", rt.note or "log non disponibili")
        if len(rt.errors) < 3:
            return CheckOutcome("runtime", "ok", f"{len(rt.errors)} errori su {rt.scanned} righe di log",
                                {"scanned": rt.scanned}, owns=("runtime_errors",))
        groups: dict[str, int] = {}
        for e in rt.errors:
            first = (e.get("message") or "").splitlines()[0][:160] or f"HTTP {e.get('status')}"
            groups[first] = groups.get(first, 0) + 1
        top = sorted(groups.items(), key=lambda x: -x[1])[:3]
        paths = sorted({e.get("path") for e in rt.errors if e.get("path")})[:5]
        title = f"{len(rt.errors)} errori del server" + (f" su {', '.join(paths)}" if paths else "")
        sig = Signal("runtime_errors", "warning", "runtime", title,
                     {"count": len(rt.errors), "top": top, "paths": paths, "samples": rt.errors[:5]})
        return CheckOutcome("runtime", "warning", title, {"top": top}, [sig], ("runtime_errors",))

    def _check_ssl(self, site: Site, obs: Observation) -> CheckOutcome:
        days = obs.cert_days
        if days is None:
            return CheckOutcome("ssl", "skipped", "certificato non verificabile")
        if days < 14:
            sev = "critical" if days < 3 else "warning"
            sig = Signal("ssl_expiring", sev, "cert", f"Il certificato HTTPS scade tra {days} giorni", {"days": days})
            return CheckOutcome("ssl", sev, sig.title, {"days": days}, [sig], ("ssl_expiring",))
        return CheckOutcome("ssl", "ok", f"certificato valido ancora {days} giorni", {"days": days},
                            owns=("ssl_expiring",))

    def _check_security(self, obs: Observation) -> CheckOutcome:
        if not obs.alerts_checked:
            return CheckOutcome("security", "skipped", "repository non collegato o token senza permesso")
        if obs.alerts is None:
            return CheckOutcome("security", "skipped", "alert Dependabot non accessibili (permesso mancante)")
        serious = [a for a in obs.alerts if a.get("severity") in ("critical", "high")]
        if serious:
            pk = ", ".join(sorted({a.get("package") or "?" for a in serious})[:5])
            sig = Signal("vulnerabilities", "warning", "deps",
                         f"{len(serious)} {'vulnerabilità grave' if len(serious) == 1 else 'vulnerabilità gravi'} "
                         f"nelle dipendenze ({pk})",
                         {"alerts": obs.alerts[:10], "serious": len(serious)})
            return CheckOutcome("security", "warning", sig.title, {"total": len(obs.alerts)}, [sig],
                                ("vulnerabilities",))
        note = f"{len(obs.alerts)} avvisi minori" if obs.alerts else "nessuna vulnerabilità nota"
        return CheckOutcome("security", "ok", note, {"total": len(obs.alerts)}, owns=("vulnerabilities",))

    def _check_ci(self, obs: Observation) -> CheckOutcome:
        if obs.ci_status is None:
            return CheckOutcome("ci", "skipped", "stato CI non disponibile")
        if obs.ci_status == "failure":
            sig = Signal("ci_failing", "warning", "ci", "La CI sul branch principale è in errore", {})
            return CheckOutcome("ci", "warning", sig.title, {}, [sig], ("ci_failing",))
        return CheckOutcome("ci", "ok", f"CI {obs.ci_status}", owns=("ci_failing",))

    # ------------------------------------------------------------------ evaluate + incidents
    async def evaluate(self, site: Site, obs: Observation, full: bool) -> dict:
        run = self.run
        assert run is not None
        outcomes = self.checks(site, obs, full)
        for o in outcomes:
            self.s.add(SiteCheck(run_id=run.id, site_id=site.id, check=o.check, status=o.status, summary=o.summary,
                                 details=o.details, duration_ms=obs.duration_ms if o.check == "uptime" else 0))
        new, resolved = self.apply_incidents(site, outcomes, obs)
        evaluated = [o for o in outcomes if o.status not in ("skipped", "error")]
        worst = max((STATUS_RANK[o.status] for o in evaluated), default=0)
        site.status = {3: "critical", 2: "warning", 1: "ok"}.get(worst, "unknown")
        bad = [o for o in evaluated if o.status in ("critical", "warning")]
        site.status_note = bad[0].summary if bad else (obs.errors[0] if obs.errors else None)
        site.last_checked_at = datetime.now(UTC)
        if obs.production:
            site.current_deployment_id = obs.production.id
        uptime = next(o for o in outcomes if o.check == "uptime")
        pages_critical = any(s.severity == "critical" for o in outcomes if o.check == "pages" for s in o.signals)
        if uptime.status == "ok" and not pages_critical and obs.production and obs.production.state == READY:
            site.last_good_deployment_id = obs.production.id
            site.last_good_commit_sha = obs.production.commit_sha
        self.s.flush()
        await self.respond(site, obs)
        self._refresh_status(site, evaluated)
        await self._notify_new_critical(site, new)
        return {"checks": len(outcomes), "new": len(new), "resolved": len(resolved)}

    def _refresh_status(self, site: Site, evaluated: list[CheckOutcome]) -> None:
        """After remediation the picture may have changed (rollback verified, build rebuilt): the site status is
        the worst of its still-active incidents (a mitigated one counts as a warning) and of signal-less warnings."""
        if not evaluated:
            return
        rank = max((STATUS_RANK[o.status] for o in evaluated if not o.signals), default=1)
        for inc in self._open_incidents(site):
            sev = "warning" if inc.status == "mitigated" else inc.severity
            rank = max(rank, STATUS_RANK.get(sev, 1) if sev != "info" else 1)
        new = {3: "critical", 2: "warning"}.get(rank, "ok")
        if new != site.status:
            site.status = new
            active = sorted(self._open_incidents(site), key=lambda i: (i.status != "open", -SEVERITY_RANK[i.severity]))
            site.status_note = (active[0].title if active[0].status == "open" else
                                f"Tamponato ({active[0].resolution}); da correggere: {active[0].title}") if active else None

    def _open_incidents(self, site: Site) -> list[Incident]:
        return list(self.s.scalars(select(Incident).where(Incident.site_id == site.id,
                                                          Incident.status.in_(("open", "mitigated")))))

    def apply_incidents(self, site: Site, outcomes: list[CheckOutcome], obs: Observation) -> tuple[list, list]:
        now = datetime.now(UTC)
        current = {i.fingerprint: i for i in self._open_incidents(site)}
        seen: set[str] = set()
        new: list[Incident] = []
        for o in outcomes:
            for sig in o.signals:
                fp = f"{site.id}:{sig.kind}:{sig.key}"
                seen.add(fp)
                inc = current.get(fp)
                if inc is None:
                    inc = Incident(organization_id=self.org, site_id=site.id, fingerprint=fp, kind=sig.kind,
                                   severity=sig.severity, title=sig.title, details=sig.details, status="open",
                                   first_seen_at=now, last_seen_at=now)
                    self.s.add(inc)
                    current[fp] = inc
                    new.append(inc)
                    self.events.emit("night.incident.opened", {"site": site.name, "kind": sig.kind,
                                                               "severity": sig.severity, "title": sig.title},
                                     request_id=self.request_id, organization_id=self.org)
                else:
                    inc.occurrences += 1
                    inc.last_seen_at = now
                    inc.title, inc.details = sig.title, sig.details
                    if SEVERITY_RANK[sig.severity] > SEVERITY_RANK.get(inc.severity, 0):
                        inc.severity = sig.severity
                    if inc.status == "mitigated" and sig.kind in ("site_down", "page_broken"):
                        inc.status = "open"   # the mitigation did not hold
        resolved: list[Incident] = []
        for o in outcomes:
            if o.status in ("skipped", "error"):
                continue
            for fp, inc in current.items():
                if inc.kind in o.owns and fp not in seen and inc.status in ("open", "mitigated"):
                    inc.status, inc.resolved_at = "resolved", now
                    inc.resolution = inc.resolution or "tornato regolare"
                    resolved.append(inc)
                    self.events.emit("night.incident.resolved", {"site": site.name, "kind": inc.kind,
                                                                 "resolution": inc.resolution},
                                     request_id=self.request_id, organization_id=self.org)
        self.s.flush()
        return new, resolved

    # ------------------------------------------------------------------ respond
    def _action(self, site: Site | None, incident: Incident | None, action: str, status: str, summary: str,
                details: dict | None = None, approval_id: str | None = None) -> NightAction:
        a = NightAction(organization_id=self.org, run_id=self.run.id if self.run else None,
                        site_id=site.id if site else None, incident_id=incident.id if incident else None,
                        action=action, status=status, summary=summary, details=details or {}, approval_id=approval_id)
        self.s.add(a)
        self.s.flush()
        self.events.emit("night.action", {"site": site.name if site else None, "action": action, "status": status,
                                          "summary": summary}, request_id=self.request_id, organization_id=self.org)
        return a

    def _tried(self, incident: Incident, action: str, key: str | None = None) -> bool:
        q = select(NightAction).where(NightAction.incident_id == incident.id, NightAction.action == action)
        for a in self.s.scalars(q):
            if key is None or (a.details or {}).get("key") == key:
                return True
        return False

    async def respond(self, site: Site, obs: Observation) -> None:
        incidents = {i.kind: i for i in self._open_incidents(site)}
        self.tools.policy = self._policy(site.policy or {})
        try:
            if (inc := incidents.get("site_down")) and inc.status == "open":
                await self._try_rollback(site, obs, inc)
            if inc := incidents.get("deploy_failed"):
                await self._try_redeploy(site, obs, inc)
            await self._try_fixes(site, obs, incidents)
        finally:
            self.tools.policy = self.base_policy

    def _rollback_target(self, site: Site, obs: Observation) -> tuple[Deployment | None, str]:
        prod = obs.production
        if prod is None:
            return None, "non so quale versione sia in produzione"
        if site.last_good_deployment_id:
            if site.last_good_deployment_id == prod.id:
                return None, "la versione in produzione era sana fino a poco fa: il problema non viene dall'ultimo deploy"
            good = next((d for d in obs.recent if d.id == site.last_good_deployment_id), None)
            if good and good.state == READY:
                return good, "ultima versione verificata sana"
        # no verified history: only roll back a *recent* deploy, to the deployment right before it
        created = _dt(prod.created_at)
        if created and datetime.now(UTC) - created < timedelta(hours=48):
            older = [d for d in obs.recent if d.state == READY and d.id != prod.id
                     and (_dt(d.created_at) or created) < created]
            if older:
                return older[0], "versione precedente all'ultimo deploy"
        return None, "nessuna versione precedente sicura a cui tornare"

    async def _try_rollback(self, site: Site, obs: Observation, inc: Incident) -> None:
        if not (self.deps.vercel and site.vercel_project_id):
            if not self._tried(inc, "escalate"):
                self._action(site, inc, "escalate", "skipped", "Sito non su Vercel: non posso intervenire, ti avviso.",
                             {"key": "no-vercel"})
            return
        target, why = self._rollback_target(site, obs)
        if target is None:
            if not self._tried(inc, "rollback", "none"):
                self._action(site, inc, "rollback", "skipped", f"Nessun rollback: {why}.", {"key": "none"})
            return
        if self._tried(inc, "rollback", target.id):
            return
        bad = obs.production
        reason = (f"{site.name} non risponde dopo il deploy «{bad.commit_message if bad else '?'}». "
                  f"Torno a «{target.commit_message or target.id}» ({why}).")
        res = await self.tools.run("vercel", "rollback", {"project_id": site.vercel_project_id,
                                                          "deployment_id": target.id},
                                   request_id=self.request_id, reason=reason)
        details = {"key": target.id, "to_deployment": target.id, "to_commit": target.commit_sha,
                   "to_message": target.commit_message, "from_deployment": bad.id if bad else None,
                   "from_commit": bad.commit_sha if bad else None, "from_message": bad.commit_message if bad else None}
        if res.status == "pending_approval":
            self._action(site, inc, "rollback", "pending_approval",
                         f"Ho preparato il rollback a «{target.commit_message}»: aspetta il tuo ok.", details,
                         approval_id=res.approval_id)
            return
        if not res.ok:
            self._action(site, inc, "rollback", "failed", f"Rollback non riuscito: {res.error}", details)
            return
        await asyncio.sleep(self.deps.verify_delay)
        check = await self.deps.prober.probe(_join_url(site.url, "/"))
        if not check.ok and not check.protected:
            await asyncio.sleep(self.deps.retry_delay)
            check = await self.deps.prober.probe(_join_url(site.url, "/"))
        details["verify"] = {"status": check.status, "latency_ms": check.latency_ms, "error": check.error}
        if check.ok:
            inc.status = "mitigated"
            inc.resolution = f"rollback a «{target.commit_message or target.id}» alle {_clock(datetime.now(UTC), self.tz)}"
            site.status, site.status_note = "warning", f"Ripristinato con rollback; da correggere: {inc.title}"
            site.current_deployment_id = target.id
            for other in self._open_incidents(site):  # errors produced by the deploy we just took offline
                if other.id != inc.id and other.status == "open" and other.kind in ("runtime_errors", "page_broken",
                                                                                     "js_errors"):
                    other.status = "mitigated"
                    other.resolution = f"causato dal deploy annullato con il rollback ({inc.resolution})"
            # record the post-rollback reality so the site page does not show the pre-rollback checks as current
            self.s.add(SiteCheck(run_id=self.run.id, site_id=site.id, check="deploy", status="ok",
                                 summary=f"dopo il rollback in produzione «{target.commit_message or target.id}»",
                                 details={"deployment_id": target.id, "rollback": True}))
            self.s.add(SiteCheck(run_id=self.run.id, site_id=site.id, check="uptime", status="ok",
                                 summary=f"dopo il rollback: online ({check.status}, {check.latency_ms} ms)",
                                 details={"status": check.status, "rollback": True}))
            self._action(site, inc, "rollback", "verified",
                         f"Ho riportato il sito alla versione «{target.commit_message or target.id}»: ora risponde "
                         f"({check.status}, {check.latency_ms} ms).", details)
        else:
            self._action(site, inc, "rollback", "failed",
                         f"Ho fatto il rollback ma il sito resta giù ({check.short()}): serve il tuo intervento.", details)

    async def _try_redeploy(self, site: Site, obs: Observation, inc: Incident) -> None:
        d = inc.details or {}
        dep_id = d.get("deployment_id")
        if not d.get("transient") or not dep_id or not self.deps.vercel or self._tried(inc, "redeploy"):
            return
        name = site.vercel_project_name or (obs.project.name if obs.project else site.name)
        res = await self.tools.run("vercel", "redeploy", {"project_name": name, "deployment_id": dep_id},
                                   request_id=self.request_id,
                                   reason=f"Build di {site.name} fallita per un errore temporaneo: la rilancio una volta.")
        details = {"key": dep_id, "from_deployment": dep_id}
        if res.status == "pending_approval":
            self._action(site, inc, "redeploy", "pending_approval", "Rilancio della build pronto: aspetta il tuo ok.",
                         details, approval_id=res.approval_id)
            return
        if not res.ok:
            self._action(site, inc, "redeploy", "failed", f"Non sono riuscito a rilanciare la build: {res.error}", details)
            return
        new_id = res.output.get("id")
        details["new_deployment"] = new_id
        final = await self._wait_deployment(new_id, self.settings.night_preview_wait_seconds)
        if final and final.state == READY:
            inc.status, inc.resolved_at = "resolved", datetime.now(UTC)
            inc.resolution = "build rilanciata: ora è online"
            self._action(site, inc, "redeploy", "verified",
                         "La build era fallita per un errore di rete: l'ho rilanciata e ora è online.", details)
        elif final and final.state == "ERROR":
            inc.details = d | {"transient": False, "redeploy_failed": True}
            self._action(site, inc, "redeploy", "failed", "Ho rilanciato la build ma è fallita di nuovo: non è un "
                                                          "errore temporaneo.", details)
        else:
            self._action(site, inc, "redeploy", "done", "Ho rilanciato la build: la verifico al prossimo giro.", details)

    async def _wait_deployment(self, deployment_id: str | None, timeout: float) -> Deployment | None:
        if not deployment_id or not self.deps.vercel:
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                d = await self.deps.vercel.get_deployment(deployment_id)
            except Exception:
                return None
            if d.state in (READY, "ERROR", "CANCELED") or loop.time() >= deadline:
                return d
            await asyncio.sleep(self.deps.poll_interval)

    # ------------------------------------------------------------------ fixes
    def spent(self, *, run_ids: list[str] | None = None, hours: float = 14) -> float:
        """Model spend (USD, estimated) of the given runs, or of every run in the last `hours`."""
        q = select(NightRun.request_id).where(NightRun.organization_id == self.org)
        if run_ids is None:
            q = q.where(NightRun.started_at >= datetime.now(UTC) - timedelta(hours=hours))
        else:
            q = q.where(NightRun.id.in_(run_ids))
        request_ids = list(self.s.scalars(q))
        if not request_ids:
            return 0.0
        return float(self.s.scalar(select(func.coalesce(func.sum(InferenceRun.estimated_cost), 0.0))
                                   .where(InferenceRun.request_id.in_(request_ids))) or 0.0)

    def _fixes_tonight(self) -> int:
        since = datetime.now(UTC) - timedelta(hours=14)
        return self.s.scalar(select(func.count()).select_from(FixProposal).where(
            FixProposal.organization_id == self.org, FixProposal.created_at >= since)) or 0

    async def _try_fixes(self, site: Site, obs: Observation, incidents: dict[str, Incident]) -> None:
        eligible = []
        # the root cause gets the fix: a mitigated site_down (rolled back) still needs its code fixed, while errors
        # that the rollback already explained ("causato dal deploy annullato") do not need a second proposal
        if inc := incidents.get("site_down"):
            eligible.append(inc)
        elif (inc := incidents.get("page_broken")) and inc.status == "open":
            eligible.append(inc)
        if (inc := incidents.get("deploy_failed")) and not (inc.details or {}).get("transient"):
            eligible.append(inc)
        if (inc := incidents.get("runtime_errors")) and inc.status == "open":
            eligible.append(inc)
        # one proposal per site at a time: the incidents of a site usually share one root cause (a bad deploy
        # takes the site down AND fills the logs with errors) and the evidence of all of them goes into it
        if self.s.scalars(select(FixProposal).where(FixProposal.site_id == site.id,
                                                    FixProposal.status == "proposed")).first():
            return
        for inc in eligible:
            if self.s.scalars(select(FixProposal).where(FixProposal.incident_id == inc.id)).first():
                continue
            if self._tried(inc, "fix_pr"):
                continue
            await self._propose_fix(site, obs, inc)
            return

    def _evidence(self, site: Site, obs: Observation, inc: Incident) -> Evidence:
        d = inc.details or {}
        lines: list[str] = [f"Incidente: {inc.title}"]
        bad_sha = None
        failing: list[str] = []
        if inc.kind == "deploy_failed":
            bad_sha = d.get("commit_sha")
            lines += ["Log della build (ultime righe):", *obs.build_log[-60:]]
        else:
            rollback = self.s.scalars(select(NightAction).where(NightAction.incident_id == inc.id,
                                                                NightAction.action == "rollback")).first()
            bad_sha = ((rollback.details or {}).get("from_commit") if rollback else None) or \
                (obs.production.commit_sha if obs.production else None)
            if d.get("path"):
                failing.append(d["path"])
            failing += list(d.get("paths") or [])
            for _page, r, _ in obs.probes:
                if not r.ok:
                    lines.append(f"Controllo {r.url}: {r.short()}")
                    failing.append(_page["path"])
            if obs.runtime and obs.runtime.errors:
                lines.append("Errori recenti dai log di Vercel:")
                lines += [f"- {e.get('path') or ''} {e.get('status') or ''} {e.get('message')}" for e in obs.runtime.errors[:8]]
            for e in d.get("samples") or []:
                lines.append(f"- {e.get('path') or ''} {e.get('message')}")
        if obs.latest and obs.latest.state == "ERROR" and inc.kind != "deploy_failed" and obs.build_log:
            lines += ["Ultima build fallita:", *obs.build_log[-20:]]
        good = site.last_good_commit_sha if site.last_good_commit_sha != bad_sha else None
        return Evidence(summary=inc.title, error_text="\n".join(lines), bad_sha=bad_sha, good_sha=good,
                        failing_paths=sorted(set(failing)) or ["/"])

    async def _propose_fix(self, site: Site, obs: Observation, inc: Incident) -> None:
        st = self.settings
        if not self._site_flag(site.policy or {}, "fix_prs", st.night_fix_prs):
            return
        if not (self.deps.repo and site.repo_owner and site.repo_name):
            self._action(site, inc, "fix_pr", "skipped", "Nessun repository GitHub collegato: non posso preparare "
                                                         "una correzione.", {"key": "no-repo"})
            return
        if self._fixes_tonight() >= st.night_max_fixes:
            self._action(site, inc, "fix_pr", "skipped", f"Limite di {st.night_max_fixes} correzioni per notte "
                                                         "raggiunto.", {"key": "limit"})
            return
        if self.spent() >= st.night_max_cost_usd:
            self._action(site, inc, "fix_pr", "skipped", f"Budget AI della notte (${st.night_max_cost_usd:.2f}) "
                                                         "esaurito.", {"key": "budget"})
            return
        ev = self._evidence(site, obs, inc)
        agent = FixAgent(self.deps.repo, self.fix_router, self.request_id)
        try:
            draft = await agent.draft(site, inc, ev, site.sensitivity)
        except Exception as e:
            self._action(site, inc, "fix_pr", "failed", f"Analisi del codice non riuscita: {str(e)[:200]}", {"key": "error"})
            return
        if not draft.ok:
            summary = "Ho analizzato il problema ma non ho una correzione sicura"
            summary += f": {draft.reason}." if draft.reason else "."
            self._action(site, inc, "fix_pr", "skipped", summary,
                         {"key": "no-fix", "diagnosis": draft.diagnosis, "model": draft.model})
            if draft.diagnosis:
                inc.details = (inc.details or {}) | {"diagnosis": draft.diagnosis}
            return
        await self._publish_draft(site, inc, draft, ev.failing_paths)

    async def _publish_draft(self, site: Site, inc: Incident | None, draft, failing_paths: list[str],
                             instruction: str | None = None) -> FixProposal | None:
        """Branch + one commit + PR + preview check + merge approval, for a night fix (inc) or a change you asked
        for in conversation (instruction). The merge is level 4: it always waits for you.
        Before the PR a different model reviews the diff (nodo/night/reviewer.py); "bloccante" = no PR."""
        is_change = inc is None
        review_line = ""
        self.last_review = None
        if self.settings.code_review:
            from nodo.night.reviewer import review_draft
            rv = await review_draft(self.fix_router, self.request_id, site_name=site.name,
                                    request_text=instruction or (inc.title if inc else ""), diff=draft.diff,
                                    writer=draft.model, sensitivity=site.sensitivity)
            self.last_review = rv
            if rv.verdict == "bloccante":
                self._action(site, inc, "change" if is_change else "fix_pr", "skipped",
                             f"Il secondo controllo ({rv.model}) ha bloccato la modifica: {rv.note}",
                             {"key": "review-blocked", "review": rv.note, "reviewer": rv.model,
                              "diagnosis": draft.diagnosis})
                return None
            if rv.verdict == "dubbi":
                draft.confidence = "low"
                draft.diagnosis = (f"{draft.diagnosis} · Secondo parere ({rv.model}): {rv.note}").strip(" ·")
                review_line = f"**Secondo controllo ({rv.model}): DUBBI.** {rv.note}\n\n"
            elif rv.verdict == "ok":
                review_line = f"**Secondo controllo ({rv.model}):** ok. {rv.note}\n\n"
            else:
                review_line = f"**Secondo controllo:** non eseguito ({rv.note}).\n\n"
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        slug = re.sub(r"[^a-z0-9]+", "-", site.name.lower()).strip("-")[:30] or "site"
        branch = f"nodo/{'change' if is_change else 'fix'}-{slug}-{stamp}"
        what = f"Richiesta: {instruction}" if is_change else f"Problema: {inc.title}"
        noun = "la modifica" if is_change else "la correzione"
        head = ("## Modifica preparata da NODO su tua richiesta" if is_change
                else "## Correzione preparata da NODO durante il turno di notte")
        body = (f"{head}\n\n**{what.split(': ', 1)[0]}:** {what.split(': ', 1)[1]}\n\n"
                f"**{'Cosa ho cambiato' if is_change else 'Diagnosi'}:** {draft.diagnosis}\n\n**File modificati:**\n"
                + "\n".join(f"- `{x['path']}` (+{x['added']} / -{x['removed']})" for x in draft.stats)
                + f"\n\n**Come verificare:** {draft.test_hint or 'apri l anteprima e controlla la pagina.'}\n\n"
                + review_line
                + f"Confidenza del modello: {draft.confidence} · modello: {draft.model}\n\n"
                  "> Non unire senza aver visto l'anteprima. Puoi approvare o rifiutare dal pannello di NODO.")
        res = await self.tools.run("github", "open_fix_pr", {
            "owner": site.repo_owner, "name": site.repo_name, "base_branch": site.default_branch, "branch": branch,
            "files": draft.files, "message": f"{draft.title}\n\nProposta da NODO. {what}",
            "title": f"[NODO] {draft.title}", "body": body}, request_id=self.request_id,
            reason=f"{noun.capitalize()} proposta per {site.name}: {what}")
        prop = FixProposal(organization_id=self.org, site_id=site.id, incident_id=inc.id if inc else None,
                           run_id=self.run.id if self.run else None, title=draft.title, diagnosis=draft.diagnosis,
                           confidence=draft.confidence, files=draft.stats, diff=draft.diff, branch=branch,
                           model=draft.model, status="proposed", preview_note=",".join(failing_paths))
        self.s.add(prop)
        self.s.flush()
        self._fixes_this_run += 1
        if res.status == "pending_approval":
            prop.status, prop.approval_id = "proposed", res.approval_id
            prop.preview_status = "unknown"
            self._action(site, inc, "fix_pr", "pending_approval",
                         f"Ho preparato {noun}: aspetta il tuo ok per aprire la pull request.",
                         {"key": "pr", "fix_id": prop.id}, approval_id=res.approval_id)
            return prop
        if not res.ok:
            prop.status = "failed"
            self._action(site, inc, "fix_pr", "failed", f"Non sono riuscito ad aprire la pull request: {res.error}",
                         {"key": "pr", "fix_id": prop.id})
            return prop
        prop.commit_sha, prop.pr_number, prop.pr_url = res.output.get("sha"), res.output.get("pr_number"), \
            res.output.get("pr_url")
        self.s.commit()   # the preview can take minutes: release the database meanwhile
        await self.check_preview(prop, site, wait=True)
        merge = await self.tools.run("github", "merge_pr", {"owner": site.repo_owner, "name": site.repo_name,
                                                            "number": prop.pr_number, "method": "squash"},
                                     request_id=self.request_id,
                                     reason=f"Pubblicare {noun} per {site.name} (PR #{prop.pr_number})")
        prop.approval_id = merge.approval_id
        preview = {"ok": "l'anteprima funziona", "failed": "ATTENZIONE: l'anteprima non funziona",
                   "protected": "l'anteprima è protetta, controllala tu", "pending": "l'anteprima è ancora in costruzione",
                   "unknown": "anteprima non verificata"}[prop.preview_status]
        self._action(site, inc, "fix_pr", "pending_approval",
                     f"Ho preparato {noun} (PR #{prop.pr_number}): {preview}. Aspetta il tuo ok per pubblicarla.",
                     {"key": "pr", "fix_id": prop.id, "pr_url": prop.pr_url, "preview_url": prop.preview_url},
                     approval_id=merge.approval_id)
        return prop

    async def propose_change(self, site: Site, instruction: str, trigger: str = "voice") -> tuple[FixProposal | None, str]:
        """A change you asked for in conversation: branch + PR + preview, then it waits for your ok to publish."""
        from nodo.night.changer import ChangeAgent, page_paths
        if not (self.deps.repo and site.repo_owner and site.repo_name):
            return None, f"{site.name} non ha un repository GitHub collegato (o manca NODO_GITHUB_TOKEN)."
        run = NightRun(organization_id=self.org, request_id=self.request_id, kind="change", trigger=trigger,
                       summary={"instruction": instruction[:500], "site": site.name})
        self.s.add(run)
        self.s.commit()   # never hold the database while the model writes code (minutes)
        self.run = run
        # you asked for it: opening the PR on a separate branch is fine; publishing still needs your ok
        self.tools.policy = PermissionPolicy(auto_approve_actions={"github.open_fix_pr", "github.close_pr"})
        prop: FixProposal | None = None
        try:
            agent = ChangeAgent(self.deps.repo, self.fix_router, self.request_id)
            draft = await agent.draft_change(site, instruction, site.sensitivity)
            if not draft.ok:
                why = draft.diagnosis or draft.reason or "non ho una modifica sicura da proporre"
                self._action(site, None, "change", "skipped", f"Modifica richiesta non preparata: {why}",
                             {"instruction": instruction[:500], "model": draft.model})
                run.status, run.summary = "done", run.summary | {"result": "skipped"}
                return None, why
            prop = await self._publish_draft(site, None, draft, page_paths(list(draft.files)), instruction)
            if prop is None:
                rv = self.last_review
                note = f"il secondo controllo ({rv.model}) l'ha bloccata: {rv.note}" if rv else "bloccata"
                run.status, run.summary = "done", run.summary | {"result": "blocked"}
                return None, note
            run.status, run.summary = "done", run.summary | {"result": prop.status, "fix_id": prop.id}
            return prop, draft.diagnosis
        except Exception as e:
            run.status, run.error = "failed", str(e)[:500]
            self._action(site, None, "change", "failed", f"Modifica richiesta non riuscita: {str(e)[:200]}",
                         {"instruction": instruction[:500]})
            return prop, f"errore: {str(e)[:200]}"
        finally:
            run.finished_at = datetime.now(UTC)
            self.s.flush()

    async def check_preview(self, prop: FixProposal, site: Site, wait: bool = False) -> str:
        v = self.deps.vercel
        if not (v and site.vercel_project_id and prop.branch):
            prop.preview_status = "unknown"
            return prop.preview_status
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (self.settings.night_preview_wait_seconds if wait else 0)
        dep: Deployment | None = None
        while True:
            try:
                deps = await v.list_deployments(site.vercel_project_id, target=None, limit=5, branch=prop.branch)
            except Exception:
                deps = []
            dep = deps[0] if deps else None
            if dep and dep.state in (READY, "ERROR", "CANCELED"):
                break
            if loop.time() >= deadline:
                break
            await asyncio.sleep(self.deps.poll_interval)
        if dep is None or dep.state not in (READY, "ERROR", "CANCELED"):
            prop.preview_status = "pending"
            return prop.preview_status
        prop.preview_url = dep.url
        if dep.state != READY:
            prop.preview_status = "failed"
            prop.preview_note = "la build dell'anteprima è fallita"
            return prop.preview_status
        secret = bypass_secret(self.settings.vercel_protection_bypass, site)
        headers = {"x-vercel-protection-bypass": secret} if secret else None
        paths = [p for p in (prop.preview_note or "/").split(",") if p.startswith("/")] or ["/"]
        results = [await self.deps.prober.probe(_join_url(dep.url, p), headers=headers) for p in paths[:4]]
        if any(r.protected for r in results):
            prop.preview_status = "protected"
            prop.preview_note = "anteprima protetta da Vercel Authentication (imposta NODO_VERCEL_PROTECTION_BYPASS)"
        elif all(r.ok for r in results):
            prop.preview_status = "ok"
            prop.preview_note = "; ".join(f"{_short_path(r.url)} {r.status}" for r in results)
        else:
            prop.preview_status = "failed"
            prop.preview_note = "; ".join(f"{_short_path(r.url)}: {r.short()}" for r in results if not r.ok)
        self.s.flush()
        return prop.preview_status

    # ------------------------------------------------------------------ follow-up of earlier proposals
    async def follow_up(self) -> None:
        props = self.s.scalars(select(FixProposal).where(FixProposal.organization_id == self.org,
                                                         FixProposal.status == "proposed"))
        for prop in props:
            site = self.s.get(Site, prop.site_id)
            if site is None:
                continue
            if self.deps.repo and prop.pr_number:
                try:
                    st = await self.deps.repo.pr_state(site.repo_owner, site.repo_name, prop.pr_number)
                except Exception:
                    st = None
                if st and st.get("merged"):
                    prop.status = "merged"
                    self._close_approval(prop, "approved")
                    self._action(site, None, "merge", "done", f"La PR #{prop.pr_number} è stata unita (da GitHub).",
                                 {"fix_id": prop.id})
                    continue
                if st and st.get("state") == "closed":
                    prop.status = "rejected"
                    self._close_approval(prop, "rejected")
                    continue
            if prop.preview_status in ("pending", "unknown"):
                await self.check_preview(prop, site)
        self.s.flush()

    def _close_approval(self, prop: FixProposal, status: str) -> None:
        from nodo.db.models import Approval
        if prop.approval_id and (a := self.s.get(Approval, prop.approval_id)) and a.status == "pending":
            a.status, a.resolved_at = status, datetime.now(UTC)

    # ------------------------------------------------------------------ notifications
    async def _notify_new_critical(self, site: Site, new: list[Incident]) -> None:
        n = self.deps.notifier
        if not (self.settings.notify_critical and n.enabled):
            return
        fresh = [i for i in new if i.severity == "critical" and not i.notified_at]
        if not fresh:
            return
        lines = [f"NODO · {site.name}"]
        for inc in fresh:
            lines.append(f"{inc.title}.")
            lines += [f"- {a.summary}" for a in self.s.scalars(select(NightAction).where(NightAction.incident_id == inc.id))]
        if await n.send("\n".join(lines)):
            for inc in fresh:
                inc.notified_at = datetime.now(UTC)
            from nodo.channels.telegram import send_decisions
            await send_decisions(n, self.s, self.org, site_id=site.id)  # e.g. the fix, with Pubblica/Rifiuta


def bypass_secret(raw: str | None, site: Site) -> str | None:
    """NODO_VERCEL_PROTECTION_BYPASS is either one secret for every project, or a per-project list:
    `progetto-a=SECRET1,progetto-b=SECRET2` (Vercel generates a different secret for each project)."""
    if not raw:
        return None
    if "=" not in raw:
        return raw.strip()
    for part in raw.split(","):
        name, _, secret = part.partition("=")
        if name.strip() in (site.vercel_project_name, site.vercel_project_id, site.name):
            return secret.strip()
    return None


def _short_path(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path or "/"
