"""Maria's team («la squadra»): the same mind, seen as the employees it replaces.

Each agent is a real slice of NODO (its tools), with live numbers from the database: what it did today, the last
thing it did, what is waiting for it. Nothing here is decoration: an agent «works» when its tools are used.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, get_db, org_id
from nodo.db.models import BoardNote, EventLog, KnowledgeSource, NightRun, Reminder, Site

router = APIRouter(prefix="/api/v1/team", dependencies=[Depends(auth)])

AGENTS = [
    {"id": "contabile", "name": "Contabile", "color": "#ffd27a",
     "replaces": "Contabile e addetta alle vendite",
     "does": "Segna vendite, spese, consegne, sospesi e crediti del negozio; controlla i conti, avvisa se qualcosa "
             "non torna e chiude la giornata.",
     "tools": {"ledger_add", "ledger_update", "ledger_report", "ledger_prices", "ledger_close_day", "ledger_stats",
               "ledger_open", "ledger_pay", "ledger_customer"}},
    {"id": "archivista", "name": "Archivista", "color": "#9fd8ff",
     "replaces": "Segretaria con la memoria di ferro",
     "does": "Ricorda persone, regole, preferenze e scherzi; rilegge le vostre conversazioni e le vecchie chat di "
             "ChatGPT per non chiederti due volte la stessa cosa.",
     "tools": {"remember", "recall", "forget", "search_conversations", "search_knowledge", "read_knowledge",
               "learn_old_chats"}},
    {"id": "notte", "name": "Turno di notte", "color": "#c9a8ff",
     "replaces": "Sistemista web",
     "does": "Ogni notte controlla i siti, sistema da sola quello che è sicuro, prepara le correzioni e al mattino ti "
             "racconta cosa è successo.",
     "tools": {"night_report", "sites_status", "check_sites", "sync_sites", "request_site_change", "list_site_files",
               "read_site_file", "site_commits", "site_deployments", "check_url", "ssl_status", "start_work",
               "nodo_updates", "nodo_health"}},
    {"id": "creativa", "name": "Creativa", "color": "#ff9ec7",
     "replaces": "Grafica e copywriter",
     "does": "Crea documenti, PDF, Excel, immagini, copioni e bozze; li mostra sullo schermo, li salva sul desktop e "
             "li apre sul PC.",
     "tools": {"create_file", "create_document", "generate_image", "save_draft", "list_drafts", "update_draft",
               "list_files", "read_file", "save_to_desktop", "open_on_pc", "show_on_screen", "close_screen",
               "read_photo"}},
    {"id": "ricercatrice", "name": "Ricercatrice", "color": "#7ff0c4",
     "replaces": "Analista di mercato",
     "does": "Cerca sul web e legge le pagine per risponderti con dati veri, non con supposizioni.",
     "tools": {"web_search", "fetch_url"}},
    {"id": "agenda", "name": "Agenda", "color": "#ffb38a",
     "replaces": "Assistente personale",
     "does": "Promemoria, lavagna, calendario, attività e progetti; ti scrive su Telegram quando serve.",
     "tools": {"set_reminder", "list_reminders", "cancel_reminder", "board_pin", "board_done", "board_list",
               "calendar_events", "today_agenda", "create_task", "complete_task", "list_tasks", "update_task",
               "projects", "clients", "create_client", "create_project", "update_project", "notify_me", "now"}},
]
NAMES = {"read_photo": "ha guardato una foto", "ledger_add": "ha segnato nel registro",
         "ledger_update": "ha corretto il registro", "ledger_report": "ha fatto i conti",
         "ledger_pay": "ha segnato un incasso", "ledger_customer": "ha aperto la scheda di un cliente",
         "remember": "ha memorizzato", "recall": "ha ripescato un ricordo", "search_conversations": "ha riletto le "
         "conversazioni", "search_knowledge": "ha cercato nelle vecchie chat", "web_search": "ha cercato sul web",
         "create_document": "ha creato un documento", "create_file": "ha creato un file",
         "generate_image": "ha creato un'immagine", "board_pin": "ha appuntato sulla lavagna",
         "set_reminder": "ha messo un promemoria", "check_sites": "ha controllato i siti"}


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=UTC)


def team_state(db: Session, org: str, settings, files=None) -> dict:
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)
    rows = db.execute(select(EventLog.payload, EventLog.created_at).where(
        EventLog.event_type == "agent.work", EventLog.created_at >= since - timedelta(days=6))
        .order_by(EventLog.created_at.desc()).limit(2000)).all()
    out = []
    for a in AGENTS:
        mine = [(p, _aware(t)) for p, t in rows if (p or {}).get("tool") in a["tools"]]
        today = [m for m in mine if m[1] and m[1] >= since and not (m[0] or {}).get("running")]   # one per job
        last = mine[0] if mine else None
        busy = bool(last and last[1] and now - last[1] < timedelta(seconds=90))
        out.append({k: v for k, v in a.items() if k != "tools"} | {
            "status": "al lavoro" if busy else "pronta",
            "today": len(today),
            "last": ({"what": NAMES.get(last[0].get("tool"), last[0].get("tool")), "ok": last[0].get("ok", True),
                      "at": last[1].isoformat()} if last else None),
            "facts": [],
        })
    by = {a["id"]: a for a in out}
    try:
        from nodo.ledger import Ledger, due
        from nodo.night.briefing import tzinfo
        led = Ledger(db, org, settings.ledger_business, today=datetime.now(tzinfo(settings.timezone)).date())
        open_pay = led.open_payments()
        by["contabile"]["facts"] = [f"{len(open_pay)} sospesi aperti",
                                    f"{sum(due(r) for r in open_pay):.0f} € da incassare",
                                    f"{len(led.pending_work())} cose da fare"]
        by["contabile"]["business"] = settings.ledger_business
    except Exception:  # noqa: BLE001 - one broken number must not hide the team
        pass
    from nodo.memory import mind
    mine = mind.own(mind.active(db, org))
    srcs = db.scalars(select(KnowledgeSource).where(KnowledgeSource.organization_id == org)).all()
    chats = sum(int((s.stats or {}).get("conversations", 0)) for s in srcs)
    left = sum(int((s.stats or {}).get("remaining", 0)) for s in srcs if s.status == "distilling")
    by["archivista"]["facts"] = [f"{len(mine)} ricordi tuoi", f"{chats} vecchie chat"] + (
        [f"{left} chat ancora da leggere"] if left else [])
    sites = db.scalar(select(func.count()).select_from(Site).where(Site.organization_id == org,
                                                                  Site.active.is_(True))) or 0
    run = db.scalars(select(NightRun).where(NightRun.organization_id == org).order_by(NightRun.started_at.desc())
                     .limit(1)).first()
    by["notte"]["facts"] = [f"{sites} siti seguiti"] + ([f"ultimo turno: {run.status}"] if run else [])
    if files is not None:
        try:
            by["creativa"]["facts"] = [f"{len(files.list(db, org))} file creati"]
        except Exception:  # noqa: BLE001
            pass
    rem = db.scalar(select(func.count()).select_from(Reminder).where(Reminder.organization_id == org,
                                                                    Reminder.status == "pending")) or 0
    notes = db.scalar(select(func.count()).select_from(BoardNote).where(BoardNote.organization_id == org,
                                                                       BoardNote.done_at.is_(None))) or 0
    by["agenda"]["facts"] = [f"{rem} promemoria in attesa", f"{notes} note sulla lavagna"]
    total_today = sum(a["today"] for a in out)
    return {"leader": {"name": settings.assistant_name, "role": "Coordina la squadra e parla con te",
                       "today": total_today},
            "agents": out}


@router.get("")
def team(db: Session = Depends(get_db), org: str = Depends(org_id), c=Depends(container)):
    return team_state(db, org, c.settings, getattr(c, "files", None))
