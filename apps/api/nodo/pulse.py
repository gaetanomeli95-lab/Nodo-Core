"""NODO Pulse: an inspectable, read-only list of priorities grounded in stored records.

No model writes these observations. Never claim a site is healthy unless a check exists;
never execute an approval from Pulse. Callers may ask the conversation brain to investigate.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.db.models import Approval, Incident, Project, Site, Task, TaskStatus
from nodo.night.briefing import latest_or_live, tzinfo


def _utc(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _date(d: datetime | None, tz: ZoneInfo) -> str | None:
    return _utc(d).astimezone(tz).date().isoformat() if d else None


def build_pulse(s: Session, org: str, settings, *, now: datetime | None = None, limit: int = 6) -> dict:
    """Return actionable observations with explicit provenance and non-executing follow-up prompts."""
    now = _utc(now or datetime.now(UTC))
    assert now is not None
    tz = tzinfo(settings.timezone)
    today = now.astimezone(tz).date().isoformat()
    limit = max(1, min(limit, 12))

    projects = {p.id: p.name for p in s.scalars(select(Project).where(Project.organization_id == org))}
    tasks = list(s.scalars(select(Task).where(Task.organization_id == org,
                                               Task.status != TaskStatus.DONE)))
    sites = {site.id: site for site in s.scalars(select(Site).where(
        Site.organization_id == org, Site.active.is_(True)))}
    incidents = list(s.scalars(select(Incident).where(Incident.organization_id == org,
                                                    Incident.status.in_(("open", "mitigated")))))
    pending = list(s.scalars(select(Approval).where(Approval.organization_id == org,
                                                   Approval.status == "pending")))

    observed: list[tuple[int, datetime, dict]] = []
    counts = {"open_tasks": len(tasks), "overdue_tasks": 0, "due_today": 0,
              "blocked_tasks": 0, "open_incidents": 0, "pending_approvals": len(pending),
              "sites_configured": len(sites), "sites_checked_last_night": 0}

    for t in tasks:
        day = _date(t.due_date, tz)
        overdue = bool(day and day < today)
        due_today = day == today
        blocked = t.status == TaskStatus.BLOCKED
        counts["overdue_tasks"] += int(overdue)
        counts["due_today"] += int(due_today)
        counts["blocked_tasks"] += int(blocked)
        if not (overdue or due_today or blocked or t.priority <= 2):
            continue
        project = projects.get(t.project_id)
        # No related project name when it belongs to a different organization.
        title = t.title
        reason = ("Scaduta il " + day if overdue else "In scadenza oggi" if due_today
                  else "Attività bloccata" if blocked else "Priorità alta registrata")
        if blocked and t.blocked_reason:
            reason += f" · {t.blocked_reason[:100]}"
        prompt = (f"NODO, esamina l'attività «{title}»"
                  + (f" del progetto «{project}»" if project else "")
                  + ", verifica quello che sai e proponi il prossimo passo concreto.")
        score = (110 if overdue else 90 if due_today else 75 if blocked else 45) + 5 - t.priority
        item = {"id": f"task:{t.id}", "kind": "task",
                "severity": "warning" if overdue or blocked else "info",
                "title": title, "detail": reason, "project": project,
                "source": "task", "source_id": t.id, "observed_at": _utc(t.updated_at).isoformat(),
                "prompt": prompt, "requires_approval": False}
        observed.append((score, _utc(t.updated_at) or now, item))

    for inc in incidents:
        site = sites.get(inc.site_id)
        if site is None:
            continue
        counts["open_incidents"] += 1
        last = _utc(inc.last_seen_at)
        checked = _utc(site.last_checked_at)
        stamp = checked.astimezone(tz).strftime("%d/%m %H:%M") if checked else "mai"
        detail = (f"{site.name} · Ultimo controllo: {stamp}. "
                  + ("Incidente mitigato, verifica necessaria." if inc.status == "mitigated"
                     else "Incidente ancora aperto nei dati registrati."))
        item = {"id": f"incident:{inc.id}", "kind": "incident",
                "severity": "critical" if inc.severity == "critical" else "warning",
                "title": inc.title, "detail": detail, "project": projects.get(site.project_id),
                "source": "incident", "source_id": inc.id, "observed_at": last.isoformat() if last else None,
                "prompt": f"NODO, controlla ora il sito «{site.name}», verifica l'incidente «{inc.title}» e dimmi cosa fare.",
                "requires_approval": False}
        score = 125 if inc.severity == "critical" else 95
        observed.append((score, last or now, item))

    for a in pending:
        item = {"id": f"approval:{a.id}", "kind": "approval", "severity": "warning",
                "title": f"Decisione in attesa · {a.tool_name}.{a.action}",
                "detail": "Serve una tua conferma esplicita. Nessuna azione eseguita automaticamente.",
                "project": None, "source": "approval", "source_id": a.id,
                "observed_at": _utc(a.created_at).isoformat(), "requires_approval": True,
                "prompt": "NODO, mostrarmi le decisioni in attesa con motivazioni e anteprime, senza approvarle."}
        observed.append((85, _utc(a.created_at) or now, item))

    # The night summary comes from persisted runs and check records; it is never inferred.
    night = latest_or_live(s, org, settings, now=now)
    counts["sites_checked_last_night"] = night.sites_checked if night.runs else 0

    observed.sort(key=lambda x: (-x[0], x[1]))
    attention = (counts["overdue_tasks"] + counts["due_today"] + counts["blocked_tasks"]
                 + counts["open_incidents"] + counts["pending_approvals"])
    if attention:
        headline = f"Ci sono {attention} segnali da valutare nei dati registrati."
    elif counts["open_tasks"] or counts["sites_configured"]:
        headline = "Non risultano urgenze registrate. Possiamo verificare cosa è cambiato."
    else:
        headline = "Non ho ancora attività o siti registrati da monitorare."

    return {"generated_at": now.isoformat(), "local_date": today,
            "timezone": str(tz), "headline": headline, "counts": counts,
            "items": [v for _, _, v in observed[:limit]],
            "night": {"runs": night.runs, "sites_checked": counts["sites_checked_last_night"],
                      "summary": night.text},
            "read_only": True}
