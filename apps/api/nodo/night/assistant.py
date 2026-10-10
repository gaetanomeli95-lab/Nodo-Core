"""Conversation bridge: lets you ask NODO (text or voice) about the night shift.

"cosa hai fatto stanotte?" -> briefing · "come stanno i siti?" -> status · "controlla i siti" -> a light check now ·
"cosa devo approvare?" -> pending decisions (the first one is bound so that a spoken "sì" approves exactly it).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy import select

from nodo.db.models import Incident, NightRun, Site


def _bind(pending: list[dict]) -> dict | None:
    if not pending:
        return None
    p = pending[0]
    return {"approval_id": p["approval_id"], "tool": p["tool"], "action": p["action"], "level": 4,
            "label": p["label"], "rebound": True}


def _ask(pending: list[dict]) -> list[str]:
    if not pending:
        return []
    more = f" (e altre {len(pending) - 1} decisioni)" if len(pending) > 1 else ""
    return [f"Vuoi che proceda: {pending[0]['label']}?{more} Dimmi sì o no."]


def _filter_sites(core, intent, sites: list[Site]) -> list[Site]:
    if intent.entity and intent.entity.entity_type == "project":
        linked = [x for x in sites if x.project_id == intent.entity.entity_id]
        if linked:
            return linked
    text = intent.text.lower()
    named = [x for x in sites if fuzz.partial_ratio(x.name.lower(), text) >= 90 and len(x.name) >= 4]
    return named or sites


def _site_lines(core, sites: list[Site], tz) -> list[str]:
    from nodo.night.briefing import _clock
    if not sites:
        return ["Non ho ancora siti da controllare. Collega Vercel (NODO_VERCEL_TOKEN) oppure aggiungi un sito dal "
                "pannello Siti."]
    ok = [x for x in sites if x.status == "ok"]
    bad = [x for x in sites if x.status in ("critical", "warning")]
    unknown = [x for x in sites if x.status not in ("ok", "critical", "warning")]
    last = max((x.last_checked_at for x in sites if x.last_checked_at), default=None)
    lines = [f"Seguo {len(sites)} {'sito' if len(sites) == 1 else 'siti'}"
             + (f" (ultimo controllo alle {_clock(last, tz)})." if last else ".")]
    if ok:
        lines.append(f"Regolari: {', '.join(x.name for x in ok)}.")
    for x in sorted(bad, key=lambda y: 0 if y.status == "critical" else 1):
        label = "PROBLEMA" if x.status == "critical" else "attenzione"
        lines.append(f"{x.name} — {label}: {x.status_note or 'vedi pannello'}.")
    if unknown:
        lines.append(f"Non ancora controllati: {', '.join(x.name for x in unknown)}.")
    return lines


async def night_lines(core, intent, request_id: str, B) -> tuple[list[str], dict | None]:
    c = core.container
    settings = core.settings
    tz = B.tzinfo(settings.timezone)
    s, org = core.s, core.org
    if intent.name == "today":
        since = datetime.now(UTC) - timedelta(hours=16)
        ran = s.scalars(select(NightRun).where(NightRun.organization_id == org, NightRun.kind.in_(("night", "light")),
                                               NightRun.started_at >= since).limit(1)).first()
        if not ran:
            return [], None
        b = B.latest_or_live(s, org, settings)
        open_items = [i for i in b.items if i["status"] in ("open", "mitigated")]
        line = f"Turno di notte: {b.sites_checked} siti controllati"
        line += f", {len(open_items)} problemi ancora aperti" if open_items else ", nessun problema aperto"
        if b.pending:
            line += f", {len(b.pending)} decisioni ti aspettano"
        return [line + ". Chiedimi \"cosa hai fatto stanotte?\" per i dettagli."], None
    if intent.name == "night_report":
        b = B.latest_or_live(s, org, settings)
        return b.text.split("\n") + _ask(b.pending), _bind(b.pending)
    if intent.name == "pending_approvals":
        pending = B.pending_decisions(s, org)
        if not pending:
            return ["Non c'è niente da approvare."], None
        lines = [f"Hai {len(pending)} {'decisione' if len(pending) == 1 else 'decisioni'} in attesa:"]
        lines += [f"- {p['label']}" + (f" (anteprima: {B.PREVIEW_IT.get(p['preview_status'], '')})"
                                       if p.get("preview_status") else "") for p in pending]
        return lines + _ask(pending), _bind(pending)
    sites = list(s.scalars(select(Site).where(Site.organization_id == org, Site.active.is_(True)).order_by(Site.name)))
    focus = _filter_sites(core, intent, sites)
    if intent.name == "sites_status":
        return _site_lines(core, focus, tz), None
    if intent.name == "run_checks":
        if c is None:
            return ["Il turno di notte non è attivo in questa istanza."], None
        if not sites:
            return _site_lines(core, [], tz), None
        sched = getattr(c, "scheduler", None)
        if sched is not None and sched._lock.locked():
            return ["C'è già un giro di controllo in corso: tra poco trovi i risultati nel pannello Notte."], None
        shift = c.night_shift(s)
        ids = [x.id for x in focus] if len(focus) < len(sites) else None
        try:
            run = await asyncio.wait_for(shift.run_pass("light", "command", ids), timeout=180)
        except TimeoutError:
            return ["Il controllo stava impiegando più di 3 minuti e l'ho interrotto: avvia un giro dal pannello "
                    "Notte, lì può durare quanto serve."], None
        checked = [x for x in focus]
        for x in checked:
            s.refresh(x)
        lines = [f"Ho appena controllato {len(checked)} {'sito' if len(checked) == 1 else 'siti'}."]
        lines += _site_lines(core, checked, tz)[1:]
        acts = run.summary.get("actions") or {}
        if acts:
            lines.append("Azioni: " + ", ".join(f"{k} ×{v}" for k, v in acts.items()) + ".")
        opened = s.scalars(select(Incident).where(Incident.organization_id == org,
                                                  Incident.status.in_(("open", "mitigated")))).all()
        if not opened and all(x.status == "ok" for x in checked):
            lines.append("Tutto regolare.")
        return lines, None
    return [], None
