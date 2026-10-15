"""One-off corrections of the register, applied once at start-up, each only if the rows are still exactly as
they were found (nothing is ever deleted: a wrong row becomes «annullato», it stays visible and can be restored).

2026-10-15 (Gaetano: «il PDF ha dati sbagliati»):
- the four expenses of 13/10 written twice 30 seconds apart (110 € counted as 220 €): the second copy is annulled;
- Rachid's sale written twice (first «pagato», then again «sospeso» after the deposit): the second copy is
  annulled, the first becomes «sospeso» and the 250 € deposit he left is recorded: 100 € still to pay.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from nodo.db.models import EventLog, LedgerEntry

log = logging.getLogger("nodo.fixes")
FIX_ID = "registro-doppioni-2026-10-15"
_SPESE_DOPPIE = {"684076c8": 40.0, "03e816c0": 50.0, "3a4009ff": 10.0, "65fd0bc2": 10.0}
_RACHID_DOPPIE = {"fe365df3": 140.0, "09a2374c": 70.0}
_RACHID_VERE = {"fc7484da": 280.0, "63f7f4d6": 70.0}


def _row(db, prefix: str) -> LedgerEntry | None:
    return db.scalars(select(LedgerEntry).where(LedgerEntry.id.like(prefix + "%"))).first()


def apply(db, business: str = "") -> list[str]:
    if db.scalars(select(EventLog).where(EventLog.event_type == "data.fix",
                                         EventLog.payload["fix"].as_string() == FIX_ID)).first():
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
