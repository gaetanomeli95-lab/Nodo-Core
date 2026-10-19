"""One-off corrections of the register, applied once at start-up, each only if the rows are still exactly as
they were found (nothing is ever deleted: a wrong row becomes «annullato», it stays visible and can be restored).

2026-10-15 (Gaetano: «il PDF ha dati sbagliati»):
- the four expenses of 13/10 written twice 30 seconds apart (110 € counted as 220 €): the second copy is annulled;
- Rachid's sale written twice (first «pagato», then again «sospeso» after the deposit): the second copy is
  annulled, the first becomes «sospeso» and the 250 € deposit he left is recorded: 100 € still to pay.

2026-10-19 (Gaetano: «la riga 1410 va tolta, è il totale di tutta la merce»): Enrico Patti Cash's lump
«sospeso … 1410 €» was the total of the pieces now written one by one, so it counted his debt twice: annulled.

2026-10-19 (the agenda for Claude of 18/10): Maria said «l'ho aggiunto all'agenda» seven times, but only the first
point became a task. The other points are written now, each with its true state (done today, or still to do).
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from nodo.db.models import EventLog, LedgerEntry, Task, TaskStatus

log = logging.getLogger("nodo.fixes")
FIX_ID = "registro-doppioni-2026-10-15"
_SPESE_DOPPIE = {"684076c8": 40.0, "03e816c0": 50.0, "3a4009ff": 10.0, "65fd0bc2": 10.0}
_RACHID_DOPPIE = {"fe365df3": 140.0, "09a2374c": 70.0}
_RACHID_VERE = {"fc7484da": 280.0, "63f7f4d6": 70.0}


def _row(db, prefix: str) -> LedgerEntry | None:
    return db.scalars(select(LedgerEntry).where(LedgerEntry.id.like(prefix + "%"))).first()


def _done(db, fix_id: str) -> bool:
    return db.scalars(select(EventLog).where(EventLog.event_type == "data.fix",
                                             EventLog.payload["fix"].as_string() == fix_id)).first() is not None


def apply(db, business: str = "") -> list[str]:
    return _doppioni(db, business) + _patti_totale(db) + _agenda_18_10(db)


AGENDA_FIX_ID = "agenda-claude-2026-10-18"
_AGENDA = [  # (title, done today?)
    ("Animazioni e grafica mai viste: il «tocco in più» dell'interfaccia", False),
    ("Memoria predittiva: imparare le routine di Gaetano e anticipare le necessità", True),
    ("Studiare come funzionano ChatGPT, Claude e Gemini e farlo a modo nostro", True),
    ("Pop-up che non si aprono o si chiudono subito", True),
    ("Risposte in inglese o in altre lingue invece che in italiano", True),
    ("Finestra che mostra il lavoro in corso: cosa sta facendo e il codice mentre lo scrive", True),
    ("HostFacility: bozza del sito e logo nitido (il 18/10 non sono stati fatti)", False),
]


def _agenda_18_10(db) -> list[str]:
    if _done(db, AGENDA_FIX_ID):
        return []
    from datetime import UTC, datetime
    done: list[str] = []
    first = db.scalars(select(Task).where(Task.id.like("7b018166%"))).first()
    if first is not None:
        for title, ok in _AGENDA:
            db.add(Task(organization_id=first.organization_id, project_id=first.project_id, title=title,
                        priority=1, status=TaskStatus.DONE if ok else TaskStatus.TODO,
                        completed_at=datetime.now(UTC) if ok else None))
        done.append("agenda del 18/10 per Claude: scritti i punti che Maria aveva detto di segnare e non aveva salvato")
    db.add(EventLog(event_type="data.fix", payload={"fix": AGENDA_FIX_ID, "done": done}))
    for d in done:
        log.info("registro corretto: %s", d)
    return done


PATTI_FIX_ID = "patti-totale-1410-2026-10-19"


def _patti_totale(db) -> list[str]:
    if _done(db, PATTI_FIX_ID):
        return []
    done: list[str] = []
    r = _row(db, "bb9babf3")
    if r is not None and r.kind == "credito" and "patti" in (r.counterpart or "").lower() \
            and abs((r.amount or 0) - 1410) < 0.01 and r.status != "annullato":
        r.status = "annullato"
        r.note = ((r.note or "") + " · annullata: era il totale della merce, ora scritta pezzo per pezzo").strip(" ·")
        done.append("Enrico Patti Cash: tolta la riga da 1410 € (era il totale, i pezzi sono già scritti uno per uno)")
    db.add(EventLog(event_type="data.fix", payload={"fix": PATTI_FIX_ID, "done": done}))
    for d in done:
        log.info("registro corretto: %s", d)
    return done


def _doppioni(db, business: str = "") -> list[str]:
    if _done(db, FIX_ID):
        return []
    done: list[str] = []
    spese = {p: _row(db, p) for p in _SPESE_DOPPIE}
    if all(r is not None and r.day == "2026-10-13" and r.kind == "spesa" and r.status != "annullato"
           and abs((r.amount or 0) - _SPESE_DOPPIE[p]) < 0.01 for p, r in spese.items()):
        for r in spese.values():
            r.status = "annullato"
            r.note = ((r.note or "") + " · annullata: doppione della stessa spesa").strip(" ·")
        done.append("spese del 13/10 scritte due volte: annullata la copia (uscite 110 €, non 220 €)")
    dup = {p: _row(db, p) for p in _RACHID_DOPPIE}
    real = {p: _row(db, p) for p in _RACHID_VERE}
    rows = list(dup.values()) + list(real.values())
    if all(r is not None and r.day == "2026-10-15" and "rachid" in (r.counterpart or "").lower()
           and r.status != "annullato" for r in rows) \
            and all(abs((r.amount or 0) - _RACHID_DOPPIE[p]) < 0.01 and r.payment == "sospeso" for p, r in dup.items()) \
            and all(abs((r.amount or 0) - _RACHID_VERE[p]) < 0.01 and r.payment == "pagato" and not r.paid_amount
                    for p, r in real.items()):
        from datetime import date

        from nodo.ledger import Ledger
        for r in dup.values():
            r.status = "annullato"
            r.note = ((r.note or "") + " · annullata: doppione della vendita a Rachid").strip(" ·")
        for r in real.values():
            r.payment, r.paid_on = "sospeso", None
        db.flush()
        first = next(iter(real.values()))
        Ledger(db, first.organization_id, business or first.business or "", today=date(2026, 10, 15)).pay(
            first.counterpart, 250, day="2026-10-15", note="acconto lasciato alla consegna")
        done.append("Rachid: vendita scritta due volte, tenuta una sola (350 €), acconto 250 €, restano 100 €")
    db.add(EventLog(event_type="data.fix", payload={"fix": FIX_ID, "done": done}))
    for d in done:
        log.info("registro corretto: %s", d)
    return done
