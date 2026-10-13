"""REST routes for the night shift: sites, runs, briefing, incidents, fix proposals, setup check."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, org_id
from nodo.app import Container
from nodo.db.models import FixProposal, Incident, NightAction, NightRun, Site, SiteCheck
from nodo.db.session import get_db
from nodo.night import approvals
from nodo.night.briefing import latest_or_live, pending_decisions

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])
_tasks: set[asyncio.Task] = set()


def _iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).isoformat()


def _row(obj, *exclude: str) -> dict:
    return {c.name: (_iso(v) if isinstance(v := getattr(obj, c.name), datetime) else v)
            for c in obj.__table__.columns if c.name not in exclude}


# ---------------------------------------------------------------------------------------------- sites
class KeyPage(BaseModel):
    path: str = Field(pattern=r"^/")
    expect: str | None = None
    name: str | None = None


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(pattern=r"^https?://")
    project_id: str | None = None
    vercel_project_id: str | None = None
    repo_owner: str | None = None
    repo_name: str | None = None
    default_branch: str = "main"
    key_pages: list[KeyPage] = []
    policy: dict = {}
    sensitivity: str = "INTERNAL"
    aliases: list[str] = []


class SitePatch(BaseModel):
    name: str | None = None
    url: str | None = Field(default=None, pattern=r"^https?://")
    project_id: str | None = None
    repo_owner: str | None = None
    repo_name: str | None = None
    default_branch: str | None = None
    key_pages: list[KeyPage] | None = None
    policy: dict | None = None
    active: bool | None = None
    sensitivity: str | None = None


def _site_out(db: Session, s: Site) -> dict:
    open_n = db.scalar(select(func.count()).select_from(Incident).where(
        Incident.site_id == s.id, Incident.status.in_(("open", "mitigated")))) or 0
    return _row(s) | {"open_incidents": open_n}


@router.get("/sites")
def list_sites(include_inactive: bool = False, db: Session = Depends(get_db), org: str = Depends(org_id)):
    q = select(Site).where(Site.organization_id == org)
    if not include_inactive:
        q = q.where(Site.active.is_(True))
    rank = {"critical": 0, "warning": 1, "unknown": 2, "ok": 3}
    sites = sorted(db.scalars(q), key=lambda s: (rank.get(s.status, 2), s.name.lower()))
    return [_site_out(db, s) for s in sites]


@router.post("/sites", status_code=201)
def create_site(body: SiteIn, db: Session = Depends(get_db), org: str = Depends(org_id)):
    d = body.model_dump()
    d["key_pages"] = [p for p in d["key_pages"]]
    s = Site(organization_id=org, **d)
    db.add(s)
    db.commit()
    return _site_out(db, s)


@router.get("/sites/{site_id}")
def get_site(site_id: str, db: Session = Depends(get_db)):
    s = db.get(Site, site_id)
    if not s:
        raise HTTPException(404, "site not found")
    checks = db.scalars(select(SiteCheck).where(SiteCheck.site_id == site_id).order_by(SiteCheck.created_at.desc())
                        .limit(40)).all()
    incidents = db.scalars(select(Incident).where(Incident.site_id == site_id).order_by(Incident.last_seen_at.desc())
                           .limit(20)).all()
    actions = db.scalars(select(NightAction).where(NightAction.site_id == site_id)
                         .order_by(NightAction.created_at.desc()).limit(20)).all()
    return _site_out(db, s) | {"checks": [_row(c) for c in checks], "incidents": [_row(i) for i in incidents],
                               "actions": [_row(a) for a in actions]}


@router.patch("/sites/{site_id}")
def update_site(site_id: str, body: SitePatch, db: Session = Depends(get_db)):
    s = db.get(Site, site_id)
    if not s:
        raise HTTPException(404, "site not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    return _site_out(db, s)


@router.post("/sites/discover")
async def discover_sites(c: Container = Depends(container), db: Session = Depends(get_db)):
    if c.vercel is None:
        raise HTTPException(400, "Vercel non collegato: imposta NODO_VERCEL_TOKEN (vedi docs/ACCESSI.md)")
    shift = c.night_shift(db)
    try:
        created = await shift.discover()
    except Exception as e:
        raise HTTPException(502, f"Vercel: {e}")
    db.commit()
    return {"created": [x.name for x in created], "total": db.scalar(select(func.count()).select_from(Site))}


# ---------------------------------------------------------------------------------------------- runs
class RunIn(BaseModel):
    kind: str = Field("manual", pattern="^(manual|light|night)$")
    site_ids: list[str] | None = None
    wait: bool = False   # run inline and return the result (tests, CLI); the UI uses background runs


@router.post("/night/run")
async def start_run(body: RunIn, c: Container = Depends(container), db: Session = Depends(get_db)):
    sched = c.scheduler
    if body.wait or sched is None:
        if sched is not None and sched._lock.locked():
            raise HTTPException(409, "c'è già un giro in corso")
        run = await c.night_shift(db).run_pass(body.kind, "api", body.site_ids)
        return {"started": True, "run_id": run.id, "status": run.status, "summary": run.summary}
    if sched._lock.locked():
        raise HTTPException(409, "c'è già un giro in corso")
    task = asyncio.create_task(sched.run(body.kind, "api", body.site_ids))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"started": True, "run_id": None, "status": "running"}


@router.get("/night/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db), org: str = Depends(org_id)):
    rows = db.scalars(select(NightRun).where(NightRun.organization_id == org)
                      .order_by(NightRun.started_at.desc()).limit(min(limit, 100))).all()
    return [_row(r, "briefing") | {"has_briefing": bool(r.briefing)} for r in rows]


@router.get("/night/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    r = db.get(NightRun, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    names = {s.id: s.name for s in db.scalars(select(Site))}
    checks = db.scalars(select(SiteCheck).where(SiteCheck.run_id == run_id).order_by(SiteCheck.created_at)).all()
    actions = db.scalars(select(NightAction).where(NightAction.run_id == run_id).order_by(NightAction.created_at)).all()
    return _row(r) | {"checks": [_row(x) | {"site": names.get(x.site_id)} for x in checks],
                      "actions": [_row(a) | {"site": names.get(a.site_id)} for a in actions]}


@router.get("/night/status")
async def night_status(c: Container = Depends(container), db: Session = Depends(get_db), org: str = Depends(org_id)):
    s = c.settings
    sched = c.scheduler
    running = db.scalars(select(NightRun).where(NightRun.organization_id == org, NightRun.status == "running")
                         .limit(1)).first()
    last = db.scalars(select(NightRun).where(NightRun.organization_id == org, NightRun.kind != "briefing")
                      .order_by(NightRun.started_at.desc()).limit(1)).first()
    return {
        "enabled": s.night_enabled, "scheduler": sched is not None, "scheduler_error": getattr(sched, "last_error", None),
        "running": _row(running, "briefing") if running else None, "last_run": _row(last, "briefing") if last else None,
        "next": sched.next_events() if sched else None, "timezone": s.timezone,
        "window": {"start": s.night_start, "end": s.night_end, "interval_minutes": s.night_interval_minutes,
                   "day_interval_minutes": s.day_interval_minutes, "briefing_time": s.briefing_time},
        "policy": {"auto_rollback": s.night_auto_rollback, "auto_redeploy": s.night_auto_redeploy,
                   "fix_prs": s.night_fix_prs, "drafts": s.night_drafts, "max_cost_usd": s.night_max_cost_usd,
                   "max_fixes": s.night_max_fixes},
        "connectors": {"vercel": type(c.vercel).__name__ if c.vercel else None,
                       "github": type(c.repo).__name__ if c.repo else None,
                       "notifier": c.notifier.name if c.notifier and c.notifier.enabled else None,
                       "telegram_listening": c.telegram is not None,
                       "telegram_error": getattr(c.telegram, "last_error", None),
                       "prober": type(c.prober).__name__},
        "demo": c.demo is not None,
    }


# ---------------------------------------------------------------------------------------------- briefing
@router.get("/night/briefing")
def briefing(fresh: bool = False, c: Container = Depends(container), db: Session = Depends(get_db),
             org: str = Depends(org_id)):
    """Latest stored morning briefing (if from the last 14 hours) or a live one composed now."""
    if not fresh:
        since = datetime.now(UTC) - timedelta(hours=14)
        stored = db.scalars(select(NightRun).where(NightRun.organization_id == org, NightRun.kind == "briefing",
                                                   NightRun.started_at >= since)
                            .order_by(NightRun.started_at.desc()).limit(1)).first()
        newer = db.scalars(select(NightRun).where(NightRun.organization_id == org, NightRun.kind != "briefing",
                                                  NightRun.started_at >= stored.started_at).limit(1)).first() \
            if stored else None
        if stored and not newer:
            # pending decisions change after the briefing was written: always report them live
            return (stored.summary or {}) | {"text": stored.briefing, "stored": True,
                                             "pending": pending_decisions(db, org)}
    b = latest_or_live(db, org, c.settings)
    return b.to_dict() | {"stored": False}


@router.post("/night/briefing/send")
async def send_briefing(c: Container = Depends(container)):
    if c.scheduler is None:
        from nodo.night.scheduler import NightScheduler
        sched = NightScheduler(c)
    else:
        sched = c.scheduler
    out = await sched.briefing(trigger="api", send=True)
    return {"sent": out["sent"], "text": out["text"], "notifier": c.notifier.name if c.notifier else None}


# ---------------------------------------------------------------------------------------------- incidents
@router.get("/incidents")
def list_incidents(status: str = "active", site_id: str | None = None, limit: int = 100,
                   db: Session = Depends(get_db), org: str = Depends(org_id)):
    q = select(Incident).where(Incident.organization_id == org)
    if status == "active":
        q = q.where(Incident.status.in_(("open", "mitigated")))
    elif status != "all":
        q = q.where(Incident.status == status)
    if site_id:
        q = q.where(Incident.site_id == site_id)
    names = {s.id: s.name for s in db.scalars(select(Site).where(Site.organization_id == org))}
    rows = db.scalars(q.order_by(Incident.last_seen_at.desc()).limit(min(limit, 500))).all()
    return [_row(i) | {"site": names.get(i.site_id)} for i in rows]


@router.post("/incidents/{incident_id}/{decision}")
def incident_decision(incident_id: str, decision: str, db: Session = Depends(get_db)):
    if decision not in ("ignore", "resolve", "reopen"):
        raise HTTPException(400, "decision must be ignore|resolve|reopen")
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(404, "incident not found")
    if decision == "reopen":
        i.status, i.resolved_at = "open", None
    else:
        i.status = "ignored" if decision == "ignore" else "resolved"
        i.resolved_at = datetime.now(UTC)
        i.resolution = "segnato come risolto da te" if decision == "resolve" else "ignorato da te"
    db.commit()
    return _row(i)


@router.get("/night/actions")
def list_actions(limit: int = 50, db: Session = Depends(get_db), org: str = Depends(org_id)):
    names = {s.id: s.name for s in db.scalars(select(Site).where(Site.organization_id == org))}
    rows = db.scalars(select(NightAction).where(NightAction.organization_id == org)
                      .order_by(NightAction.created_at.desc()).limit(min(limit, 200))).all()
    return [_row(a) | {"site": names.get(a.site_id)} for a in rows]


# ---------------------------------------------------------------------------------------------- fixes
@router.get("/fixes")
def list_fixes(status: str | None = None, db: Session = Depends(get_db), org: str = Depends(org_id)):
    q = select(FixProposal).where(FixProposal.organization_id == org)
    if status:
        q = q.where(FixProposal.status == status)
    names = {s.id: s.name for s in db.scalars(select(Site).where(Site.organization_id == org))}
    rows = db.scalars(q.order_by(FixProposal.created_at.desc()).limit(50)).all()
    out = []
    for f in rows:
        inc = db.get(Incident, f.incident_id) if f.incident_id else None
        out.append(_row(f) | {"site": names.get(f.site_id), "incident_title": inc.title if inc else None})
    return out


@router.post("/fixes/{fix_id}/{decision}")
async def decide_fix(fix_id: str, decision: str, c: Container = Depends(container), db: Session = Depends(get_db)):
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve|reject")
    f = db.get(FixProposal, fix_id)
    if not f or not f.approval_id:
        raise HTTPException(404, "nessuna correzione in attesa con questo id")
    try:
        out = await approvals.resolve(db, c, f.approval_id, decision == "approve", via="api")
    except approvals.ApprovalError as e:
        raise HTTPException(409, str(e))
    db.commit()  # visible to the UI's immediate refresh and to the post-merge watcher
    return out


@router.get("/night/pending")
def pending(db: Session = Depends(get_db), org: str = Depends(org_id)):
    return pending_decisions(db, org)


# ---------------------------------------------------------------------------------------------- setup
@router.get("/setup/check")
async def setup_check(c: Container = Depends(container)):
    from nodo.doctor import as_dicts, run_checks
    checks = await run_checks(c)
    return {"checks": as_dicts(checks), "ready": not any(x.status in ("missing", "error") for x in checks)}


# keys the Accessi page is allowed to save from the panel (written to apps/api/.env, applied at once)
_SETTABLE_KEYS = {
    "NODO_GEMINI_API_KEY": "gemini_api_key",
    "NODO_GROQ_API_KEY": "groq_api_key",
    "NODO_OPENROUTER_API_KEY": "openrouter_api_key",
    "NODO_CEREBRAS_API_KEY": "cerebras_api_key",
    "NODO_MISTRAL_API_KEY": "mistral_api_key",
    "NODO_OPENAI_API_KEY": "openai_api_key",
    "NODO_ANTHROPIC_API_KEY": "anthropic_api_key",
    "NODO_GITHUB_TOKEN": "github_token",
    "NODO_VERCEL_TOKEN": "vercel_token",
    "NODO_CALENDAR_ICS_URL": "calendar_ics_url",
    "NODO_TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "NODO_TELEGRAM_CHAT_ID": "telegram_chat_id",
}
# keys that unlock a model provider: rebuild the list so they work without restarting NODO
_PROVIDER_KEYS = {"gemini_api_key", "groq_api_key", "openrouter_api_key", "openai_api_key", "anthropic_api_key",
                  "cerebras_api_key", "mistral_api_key"}


class KeyIn(BaseModel):
    name: str
    value: str


@router.post("/setup/key")
async def save_key(body: KeyIn, c: Container = Depends(container)):
    """Saves an access key from the Accessi page: into apps/api/.env and live into the settings,
    so the live voice and the extra brains activate without restarting NODO. The value is never
    returned or logged."""
    import os
    from pathlib import Path

    name = body.name.strip().upper()
    if name not in _SETTABLE_KEYS:
        raise HTTPException(400, f"Chiave non consentita: usa una di {sorted(_SETTABLE_KEYS)}")
    value = body.value.strip()
    if not value or any(ch in value for ch in "\r\n"):
        raise HTTPException(400, "Valore non valido.")
    env = Path(os.environ.get("NODO_ENV_FILE") or Path(__file__).resolve().parents[2] / ".env")
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    out, written = [], False
    for line in lines:
        key = line.split("=", 1)[0].strip().lstrip("#").strip()
        if key == name:
            if not written:
                out.append(f"{name}={value}")
                written = True
            continue                                    # drop duplicates of the same key
        out.append(line)
    if not written:
        out.append(f"{name}={value}")
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    setting = _SETTABLE_KEYS[name]
    setattr(c.settings, setting, value)
    if setting in _PROVIDER_KEYS:
        from nodo.providers.registry import build_providers
        c.providers[:] = build_providers(c.settings)    # in place: every new Core picks them up
    elif setting == "github_token" and c.settings.github_connector != "fake" \
            and c.settings.env not in ("TEST", "DEMO"):
        from nodo.tools.github import HttpGitHubConnector
        from nodo.tools.github_repo import HttpGitHubRepoClient
        c.github = HttpGitHubConnector(value)
        c.repo = HttpGitHubRepoClient(value)
    elif setting == "vercel_token" and c.settings.vercel_connector != "fake" \
            and c.settings.env not in ("TEST", "DEMO"):
        from nodo.tools.vercel import HttpVercelConnector
        c.vercel = HttpVercelConnector(value, c.settings.vercel_team_id)
    return {"ok": True, "name": name}


@router.get("/setup/telegram")
async def telegram_chats(c: Container = Depends(container)):
    """Helps find NODO_TELEGRAM_CHAT_ID: write any message to your bot, then open this page."""
    import httpx
    token = c.settings.telegram_bot_token
    if not token:
        raise HTTPException(400, "Prima imposta NODO_TELEGRAM_BOT_TOKEN nel file .env e riavvia NODO.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getUpdates")
        data = r.json()
    except Exception as e:
        raise HTTPException(502, f"Telegram non raggiungibile: {e}")
    if not data.get("ok"):
        raise HTTPException(400, "Il token del bot non è valido.")
    chats = {}
    for u in data.get("result", []):
        chat = (u.get("message") or u.get("channel_post") or {}).get("chat") or {}
        if chat.get("id"):
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    if not chats:
        return {"chats": [], "hint": "Scrivi un messaggio qualsiasi al tuo bot su Telegram, poi ricarica questa pagina."}
    return {"chats": [{"chat_id": k, "name": v} for k, v in chats.items()],
            "hint": "Copia il chat_id in NODO_TELEGRAM_CHAT_ID nel file .env e riavvia NODO."}
