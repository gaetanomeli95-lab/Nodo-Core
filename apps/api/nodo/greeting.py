"""The first words of the day: what NODO says when you open it. Built only from recorded data (like the briefing),
so it is accurate with no model at all, and short enough to be read aloud."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.db.models import Incident, Project, Reminder, Site, Task, TaskStatus
from nodo.night.briefing import tzinfo
from nodo.pulse import build_pulse


def _part_of_day(hour: int) -> tuple[str, str]:
    if hour < 5:
        return "night", "Ciao"
    if hour < 13:
        return "morning", "Buongiorno"
    if hour < 18:
        return "afternoon", "Buon pomeriggio"
    return "evening", "Buonasera"


def _names(items: list[str], limit: int = 3) -> str:
    items = items[:limit]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " e " + items[-1] if items else ""


def _shop_lines(s: Session, org: str, settings, today) -> list[str]:
    """The shop first: yesterday in numbers, what is still to deliver, who still has to pay."""
    from datetime import timedelta

    from nodo.ledger import Ledger, euro, sentence
    biz = settings.ledger_business or "il negozio"
    led = Ledger(s, org, biz, today=today)
    out: list[str] = []
    # the last day with entries before today (Monday: Saturday), not necessarily «ieri»
    last = [r.day for r in led.entries(today - timedelta(days=7), today - timedelta(days=1))]
    if last:
        day = max(last)
        from datetime import date as _date
        rep = led.report(_date.fromisoformat(day), _date.fromisoformat(day))
        n_sold = sum(e["qty"] for e in rep["entries"] if e["kind"] == "vendita" and e["status"] == "fatto")
        when = "Ieri" if (today - _date.fromisoformat(day)).days == 1 else f"Il {_date.fromisoformat(day).day}"
        t = rep["totals"]
        part = f"{when} a {biz}: {n_sold} {'pezzo venduto' if n_sold == 1 else 'pezzi venduti'}, " \
               f"incassati {euro(t['incassato'])}, uscite {euro(t['uscite'])}"
        out.append(part + (f", sospesi {euro(t['da_incassare_sospeso'])}." if t["da_incassare_sospeso"] else "."))
    todo = [r for r in led.pending_work() if r.day <= today.isoformat()]
    if todo:
        out.append("Oggi da fare: " + "; ".join(sentence(r).replace(" [da consegnare/da fare]", "")
                                                for r in todo[:4]) + ".")
    open_pay = led.open_payments()
    if open_pay:
        tot = sum(r.amount or 0 for r in open_pay)
        out.append(f"Aspetti ancora {euro(tot)} da " + _names(sorted({r.counterpart or 'un cliente'
                                                                          for r in open_pay})) + ".")
    return out


def build_greeting(s: Session, org: str, settings, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    tz = tzinfo(settings.timezone)
    local = now.astimezone(tz)
    part, hello = _part_of_day(local.hour)
    pulse = build_pulse(s, org, settings, now=now)
    counts, night = pulse["counts"], pulse["night"]
    lines = [f"{hello} {settings.owner_name}."]
    lines += _shop_lines(s, org, settings, local.date())

    sites = s.scalars(select(Site).where(Site.organization_id == org, Site.active.is_(True))).all()
    broken = s.scalars(select(Incident).where(Incident.organization_id == org,
                                              Incident.status.in_(("open", "mitigated")),
                                              Incident.severity == "critical")).all()
    broken_sites = sorted({x.name for x in sites if x.id in {i.site_id for i in broken}})
    if sites:
        if night["runs"]:
            lines.append(f"Stanotte ho controllato {night['sites_checked']} siti su {len(sites)}.")
        else:
            lines.append("Nella notte non risultano controlli: NODO potrebbe essere rimasto spento.")
        if broken_sites:
            n = len(broken_sites)
            lines.append(f"{'Un sito ha' if n == 1 else f'{n} siti hanno'} un problema serio: {_names(broken_sites)}.")
        elif night["runs"]:
            lines.append("Nessun problema serio aperto.")
    if counts["pending_approvals"]:
        n = counts["pending_approvals"]
        lines.append(f"{'C’è una decisione che aspetta' if n == 1 else f'Ci sono {n} decisioni che aspettano'} "
                     "il tuo ok.")

    today = local.date()

    def _aware(d: datetime) -> datetime:                 # SQLite returns naive datetimes: they are stored UTC
        return d if d.tzinfo else d.replace(tzinfo=UTC)

    reminders = [r for r in s.scalars(select(Reminder).where(Reminder.organization_id == org,
                                                             Reminder.status == "pending")
                                      .order_by(Reminder.remind_at))
                 if _aware(r.remind_at).astimezone(tz).date() == today]
    if reminders:
        lines.append("Oggi ti ricordo: " + _names([f"{r.text} alle {_aware(r.remind_at).astimezone(tz):%H:%M}"
                                                    for r in reminders]) + ".")

    names = {p.id: p.name for p in s.scalars(select(Project).where(Project.organization_id == org))}
    tasks = [t for t in s.scalars(select(Task).where(Task.organization_id == org, Task.status != TaskStatus.DONE))]
    if counts["overdue_tasks"]:
        lines.append(f"{counts['overdue_tasks']} attività sono in ritardo.")
    top = sorted(tasks, key=lambda t: (t.due_date is None, t.due_date or now, t.priority))[:2]
    if top:
        lines.append("In cima c’è: " + _names([t.title + (f" ({names[t.project_id]})" if t.project_id in names else "")
                                                for t in top]) + ".")
    lines.append("Da dove vuoi cominciare?")
    attention = counts["pending_approvals"] + len(broken_sites) + counts["overdue_tasks"] + len(reminders)
    return {"text": " ".join(lines), "part_of_day": part, "attention": attention,
            "local_date": local.date().isoformat(), "generated_at": now.isoformat()}
