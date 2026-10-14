"""La lavagna viva: everything Maria needs to answer AT ONCE, always ready.

Gaetano's picture: «a car that never stops running: you say "turn right" and it tells you everything, then it goes
back to its run». Before, every question about the shop started a search from zero: the text brain (15-20 s per
step on the Claude program, or nothing at all when the free quotas are spent) opened the register, read it,
answered. Now the shop is read continuously in the background into one short page of ready-made sentences (today,
yesterday, the week, who still has to pay, what is to deliver, the last prices, the recent customers). The live
voice has that page in front of it all the time and answers by reading it: one second instead of thirty. Only
actions (writing in the register, remembering, files) still go to the brain.

The page is rebuilt when the register changes and every couple of minutes, never while she is answering.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from sqlalchemy import select

from nodo.db.models import LedgerEntry

_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MAX_CHARS = 7000


def _cap(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def build(db, org: str, settings, now: datetime) -> str:
    from nodo.ledger import Ledger, euro, price_history, sentence, size_label

    today: date = now.date()
    biz = settings.ledger_business or "il negozio"
    led = Ledger(db, org, biz, today=today)
    out = [f"LAVAGNA VIVA di {biz}: dati veri del registro, letti alle {now:%H:%M} di {_DAYS[today.weekday()]} "
           f"{today:%d/%m}. Per le domande sul negozio rispondi SUBITO da qui, con queste parole e questi numeri."]

    def day_say(d: date) -> str:
        rep = led.report(d, d)
        return rep["say"] if rep["entries"] or rep["settled_earlier_sales"] else "nessun movimento segnato."

    out.append("OGGI: " + _cap(day_say(today), 1400))
    out.append("IERI: " + _cap(day_say(today - timedelta(days=1)), 1100))
    week = led.report(today - timedelta(days=6), today)
    t = week["totals"]
    goods = ", ".join(f"{g['pieces']} {g['item']}{' ' + g['size'] if g['size'] else ''}"
                      for g in sorted(week["goods_out"], key=lambda g: -g["pieces"])[:8])
    out.append(f"ULTIMI 7 GIORNI: incassato {euro(t['incassato'])}, uscite {euro(t['uscite'])}, saldo "
               f"{euro(t['saldo_del_periodo'])}" + (f"; merce uscita: {goods}" if goods else "") + ".")

    open_pay = led.open_payments()
    if open_pay:
        tot = sum(r.amount or 0 for r in open_pay)
        out.append(f"SOSPESI ({len(open_pay)}, in tutto {euro(tot)}): "
                   + _cap("; ".join(f"{sentence(r).rstrip('.')} (dal {r.day[8:10]}/{r.day[5:7]})"
                                     for r in open_pay), 1500) + ".")
    else:
        out.append("SOSPESI: nessuno, tutti hanno pagato.")

    todo = led.pending_work()
    out.append("DA FARE E CONSEGNE: " + (_cap("; ".join(f"{sentence(r).rstrip('.')} ({r.day[8:10]}/{r.day[5:7]})"
                                                       for r in todo), 900) + "." if todo else "niente in sospeso."))

    # the price list the shop taught her: last price for each article and size sold recently
    recent = db.scalars(select(LedgerEntry).where(
        LedgerEntry.organization_id == org, LedgerEntry.kind == "vendita", LedgerEntry.status != "annullato",
        LedgerEntry.unit_price.is_not(None)).order_by(LedgerEntry.day.desc(), LedgerEntry.at.desc()).limit(200)).all()
    seen: set[tuple[str, str]] = set()
    prices = []
    for r in recent:
        key = (r.item.lower(), r.size or "")
        if key in seen or not r.item:
            continue
        seen.add(key)
        hist = price_history(led, r.item, r.size)
        if hist:
            prices.append(f"{r.item} {size_label(r.size)}".strip() + f" {euro(hist[0].unit_price)}")
        if len(prices) >= 20:
            break
    if prices:
        out.append("ULTIMI PREZZI DI VENDITA: " + _cap("; ".join(prices), 1100) + ".")

    since = (today - timedelta(days=30)).isoformat()
    people: dict[str, str] = {}
    for r in db.scalars(select(LedgerEntry).where(
            LedgerEntry.organization_id == org, LedgerEntry.day >= since, LedgerEntry.counterpart.is_not(None),
            LedgerEntry.status != "annullato").order_by(LedgerEntry.day.desc(), LedgerEntry.at.desc()).limit(120)):
        name = (r.counterpart or "").strip()
        if name and name.lower() not in people and len(people) < 14:
            people[name.lower()] = f"{name}: {sentence(r).rstrip('.')} il {r.day[8:10]}/{r.day[5:7]}"
    if people:
        out.append("CLIENTI E FORNITORI RECENTI: " + _cap("; ".join(people.values()), 1100) + ".")
    return _cap("\n".join(out), MAX_CHARS)


class Board:
    """One per process: the page, when it was read and a fingerprint to know if anything changed."""

    def __init__(self) -> None:
        self.text = ""
        self.version = ""
        self.at: datetime | None = None

    def refresh(self, db, org: str, settings, now: datetime) -> bool:
        """Rebuild; True when the content changed (the clock line alone does not count)."""
        try:
            text = build(db, org, settings, now)
        except Exception:  # noqa: BLE001 - a broken register must never stop the voice
            return False
        body = text.split("\n", 1)[-1]
        version = hashlib.sha1(body.encode()).hexdigest()[:12]
        changed = version != self.version
        self.text, self.version, self.at = text, version, now
        return changed

    def fresh(self, now: datetime, max_age_s: int = 120) -> bool:
        return self.at is not None and (now - self.at).total_seconds() < max_age_s


_BOARDS: dict[str, Board] = {}


def board_for(org: str) -> Board:
    return _BOARDS.setdefault(org, Board())
