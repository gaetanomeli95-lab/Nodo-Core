"""Morning briefing: "here is what I did while you were asleep", built ONLY from what was recorded.

Deterministic and grounded (every sentence comes from NightRun/Incident/NightAction/FixProposal rows), so it is
accurate with no model at all; a language model may rephrase it in conversation, never add facts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nodo.db.models import (
    Approval,
    FixProposal,
    Incident,
    InferenceRun,
    NightAction,
    NightRun,
    Site,
    SiteCheck,
)

SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}
PREVIEW_IT = {"ok": "l'anteprima funziona", "failed": "attenzione, l'anteprima NON funziona",
              "protected": "l'anteprima è protetta, dagli un'occhiata tu", "pending": "l'anteprima è in costruzione",
              "unknown": "anteprima non verificata"}


@dataclass
class Briefing:
    text: str
    generated_at: str
    window_start: str
    window_end: str
    runs: int
    sites_checked: int
    sites_total: int
    items: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    drafts: list[dict] = field(default_factory=list)
    regular: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def tzinfo(name: str | None):
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # missing tz database (Windows without tzdata): fall back to UTC, never crash
        return UTC


def _parse_hhmm(v: str) -> time:
    h, m = (v or "00:00").split(":")[:2]
    return time(int(h), int(m))


def night_window_start(now: datetime, night_start: str, tz) -> datetime:
    """Start of the most recent night window (local night_start today, or yesterday if not reached yet)."""
    local = now.astimezone(tz)
    start = datetime.combine(local.date(), _parse_hhmm(night_start), tz)
    if local < start:
        start -= timedelta(days=1)
    return start.astimezone(UTC)


def _clock(d: datetime | None, tz) -> str:
    if d is None:
        return "?"
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(tz).strftime("%H:%M")


def _greeting(now: datetime, tz) -> str:
    h = now.astimezone(tz).hour
    return "Buongiorno" if 4 <= h < 13 else "Buon pomeriggio" if h < 18 else "Buonasera"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def pending_decisions(s: Session, org: str) -> list[dict]:
    out = []
    for a in s.scalars(select(Approval).where(Approval.organization_id == org, Approval.status == "pending")
                       .order_by(Approval.created_at)):
        fix = s.scalars(select(FixProposal).where(FixProposal.approval_id == a.id)).first()
        act = s.scalars(select(NightAction).where(NightAction.approval_id == a.id)).first()
        site = s.get(Site, fix.site_id if fix else act.site_id) if (fix or act) else None
        if fix:
            label = f"pubblicare la correzione per {site.name if site else '?'}" + \
                    (f" (PR #{fix.pr_number})" if fix.pr_number else "")
        elif a.tool_name == "vercel" and a.action == "rollback":
            label = f"riportare {site.name if site else 'il sito'} alla versione precedente (rollback)"
        elif a.tool_name == "vercel" and a.action == "redeploy":
            label = f"rilanciare la build di {site.name if site else '?'}"
        else:
            label = f"{a.tool_name}.{a.action}"
        out.append({"approval_id": a.id, "label": label, "tool": a.tool_name, "action": a.action,
                    "fix_id": fix.id if fix else None, "site": site.name if site else None,
                    "pr_url": fix.pr_url if fix else None, "preview_url": fix.preview_url if fix else None,
                    "preview_status": fix.preview_status if fix else None, "created_at": a.created_at.isoformat()})
    return out


def compose_briefing(s: Session, org: str, since: datetime, until: datetime | None = None, tz=UTC,
                     greeting: bool = True) -> Briefing:
    until = until or datetime.now(UTC)
    runs = list(s.scalars(select(NightRun).where(NightRun.organization_id == org, NightRun.kind != "briefing",
                                                 NightRun.started_at >= since, NightRun.started_at <= until)))
    run_ids = [r.id for r in runs]
    sites = {x.id: x for x in s.scalars(select(Site).where(Site.organization_id == org))}
    active = [x for x in sites.values() if x.active]
    checked_ids = set(s.scalars(select(SiteCheck.site_id).where(SiteCheck.run_id.in_(run_ids)).distinct())) \
        if run_ids else set()
    incidents = list(s.scalars(select(Incident).where(Incident.organization_id == org, or_(
        Incident.last_seen_at >= since, Incident.resolved_at >= since, Incident.status.in_(("open", "mitigated"))))))
    actions = list(s.scalars(select(NightAction).where(NightAction.organization_id == org,
                                                       NightAction.created_at >= since,
                                                       NightAction.created_at <= until).order_by(NightAction.created_at)))
    fixes = list(s.scalars(select(FixProposal).where(FixProposal.organization_id == org, or_(
        FixProposal.created_at >= since, FixProposal.status == "proposed"))))
    pending = pending_decisions(s, org)
    req_ids = [r.request_id for r in runs]
    cost = float(s.scalar(select(func.coalesce(func.sum(InferenceRun.estimated_cost), 0.0))
                          .where(InferenceRun.request_id.in_(req_ids))) or 0.0) if req_ids else 0.0

    items: list[dict] = []
    by_site: dict[str, list[Incident]] = {}
    for inc in incidents:
        by_site.setdefault(inc.site_id, []).append(inc)
    for site_id, incs in by_site.items():
        site = sites.get(site_id)
        if site is None:
            continue
        for inc in sorted(incs, key=lambda i: (SEV_ORDER.get(i.severity, 3), i.first_seen_at)):
            acts = [a for a in actions if a.incident_id == inc.id]
            fix = next((f for f in fixes if f.incident_id == inc.id), None)
            items.append({"site": site.name, "site_id": site.id, "incident_id": inc.id, "kind": inc.kind,
                          "severity": inc.severity, "title": inc.title, "status": inc.status,
                          "resolution": inc.resolution, "first_seen": _clock(inc.first_seen_at, tz),
                          "actions": [{"action": a.action, "status": a.status, "summary": a.summary,
                                       "at": _clock(a.created_at, tz)} for a in acts],
                          "fix": {"id": fix.id, "pr_number": fix.pr_number, "pr_url": fix.pr_url,
                                  "preview_status": fix.preview_status, "preview_url": fix.preview_url,
                                  "status": fix.status} if fix else None})
    site_rank: dict[str, int] = {}
    for i in items:
        site_rank[i["site"]] = min(site_rank.get(i["site"], 9), SEV_ORDER.get(i["severity"], 3))
    items.sort(key=lambda i: (site_rank[i["site"]], i["site"], SEV_ORDER.get(i["severity"], 3)))
    drafts = [{"summary": a.summary, **(a.details or {})} for a in actions if a.action == "draft" and a.status == "done"]
    draft_skips = [a.summary for a in actions if a.action == "draft" and a.status == "skipped"]
    troubled = {i["site_id"] for i in items if i["status"] in ("open", "mitigated") or i["severity"] == "critical"}
    regular = sorted(sites[i].name for i in checked_ids if i in sites and i not in troubled
                     and sites[i].status == "ok")

    now = until
    lines: list[str] = []
    head = f"{_greeting(now, tz)}. " if greeting else ""
    if not runs:
        head += "Stanotte non ho fatto controlli: il computer era spento o NODO non era avviato."
        lines.append(head)
    else:
        n_runs = len(runs)
        head += (f"Stanotte ho fatto {_plural(n_runs, 'giro', 'giri')} di controllo su "
                 f"{_plural(len(checked_ids), 'sito', 'siti')}")
        failed_runs = [r for r in runs if r.status == "failed"]
        if failed_runs:
            head += f" ({_plural(len(failed_runs), 'giro non è andato', 'giri non sono andati')} a buon fine)"
        lines.append(head + ".")
        if not items:
            lines.append("Tutto regolare: nessun problema da segnalare." if checked_ids else
                         "Non ho ancora siti da controllare: collega Vercel o aggiungine uno dal pannello.")
        else:
            n_ok = len(regular)
            if n_ok:
                lines.append(f"Regolari: {', '.join(regular)}.")
            lines.append("Cosa è successo:")
            for it in items:
                sent = f"- {it['site']}: {it['title']}"
                if it["kind"] in ("site_down", "page_broken", "runtime_errors", "js_errors"):
                    sent += f" (dalle {it['first_seen']})"
                sent += "."
                done_text = " ".join(a["summary"] for a in it["actions"] if a["action"] != "fix_pr")
                if done_text:
                    sent += " " + done_text
                fix = it["fix"]
                if fix and fix["status"] == "proposed" and fix["pr_number"]:
                    sent += (f" Ho preparato la correzione (PR #{fix['pr_number']}): "
                             f"{PREVIEW_IT.get(fix['preview_status'], 'anteprima non verificata')}. Aspetta il tuo ok.")
                elif fix and fix["status"] == "merged":
                    sent += f" La correzione (PR #{fix['pr_number']}) è stata pubblicata."
                else:
                    fix_note = next((a["summary"] for a in it["actions"] if a["action"] == "fix_pr"), None)
                    if fix_note:
                        sent += " " + fix_note
                if it["status"] == "resolved" and not done_text:
                    sent += f" Ora è risolto ({it['resolution'] or 'tornato regolare'})."
                elif it["status"] == "mitigated" and not done_text and not fix:
                    sent += f" Tamponato: {it['resolution']}."
                elif it["status"] == "open" and not done_text and not fix:
                    if it["kind"] == "vulnerabilities":
                        sent += " Te lo segnalo, non ho toccato nulla."
                    elif it["severity"] == "critical":
                        sent += " È ancora aperto: serve il tuo intervento."
                    else:
                        sent += " Ancora da sistemare."
                lines.append(sent)
    if drafts:
        lines.append("Ho preparato " + _plural(len(drafts), "bozza", "bozze") + ": " +
                     "; ".join(d["summary"].removeprefix("Bozza pronta: ").rstrip(".") for d in drafts) + ".")
    elif draft_skips:
        lines.append(draft_skips[-1])
    if pending:
        lines.append("Da decidere: " + "; ".join(p["label"] for p in pending) + ".")
    if cost >= 0.005:
        lines.append(f"Spesa AI della notte: circa ${cost:.2f}.")
    return Briefing(text="\n".join(lines), generated_at=now.isoformat(), window_start=since.isoformat(),
                    window_end=until.isoformat(), runs=len(runs), sites_checked=len(checked_ids),
                    sites_total=len(active), items=items, pending=pending, drafts=drafts, regular=regular,
                    cost_usd=round(cost, 4))


def latest_or_live(s: Session, org: str, settings, now: datetime | None = None) -> Briefing:
    """The briefing for 'the last night': from the most recent night window (minus a few evening hours)."""
    now = now or datetime.now(UTC)
    tz = tzinfo(settings.timezone)
    since = night_window_start(now, settings.night_start, tz) - timedelta(hours=6)
    return compose_briefing(s, org, since, now, tz)
