"""The shop register for the panel: a day (or a period) with every row, totals, open payments and work to do."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from nodo.api.deps import auth, container, get_db, org_id
from nodo.app import Container
from nodo.ledger import Ledger, parse_day, sentence, stats, view
from nodo.night.briefing import tzinfo

router = APIRouter(prefix="/api/v1/ledger", dependencies=[Depends(auth)])


def _ledger(c: Container, db: Session, org: str) -> Ledger:
    return Ledger(db, org, c.settings.ledger_business, today=datetime.now(tzinfo(c.settings.timezone)).date())


@router.get("")
def report(start: str | None = None, end: str | None = None, c: Container = Depends(container),
           db: Session = Depends(get_db), org: str = Depends(org_id)):
    led = _ledger(c, db, org)
    try:
        a = parse_day(start, led.today)
        b = parse_day(end or start, led.today)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if a > b:
        a, b = b, a
    rep = led.report(a, b)
    rep["business"] = c.settings.ledger_business
    rep["today"] = led.today.isoformat()
    for e, row in zip(rep["entries"], led.entries(a, b), strict=False):
        e["sentence"] = sentence(row)
    return rep


@router.get("/open")
def open_items(c: Container = Depends(container), db: Session = Depends(get_db), org: str = Depends(org_id)):
    led = _ledger(c, db, org)
    sospesi, todo = led.open_payments(), led.pending_work()
    return {"business": c.settings.ledger_business,
            "sospesi": [{**view(r), "sentence": sentence(r)} for r in sospesi],
            "totale_sospeso": round(sum(r.amount or 0 for r in sospesi), 2),
            "da_fare": [{**view(r), "sentence": sentence(r)} for r in todo]}


@router.get("/stats")
def ledger_stats(days: int = 30, c: Container = Depends(container), db: Session = Depends(get_db),
                 org: str = Depends(org_id)):
    return {"business": c.settings.ledger_business, **stats(_ledger(c, db, org), days)}
