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
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from nodo.db.models import LedgerEntry

_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MAX_CHARS = 7000


def _cap(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def build(db, org: str, settings, now: datetime) -> str:
    from nodo.ledger import Ledger, due, euro, price_history, sentence, size_label

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
        tot = sum(due(r) for r in open_pay)
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
    # every customer ever written in the register (sales, replacements, debts): «quanti clienti abbiamo?» must not
    # forget the signora Silvana because her row was a replacement and not a sale
    names = sorted({(r or "").strip() for r in db.scalars(select(LedgerEntry.counterpart).where(
        LedgerEntry.organization_id == org, LedgerEntry.kind.in_(("vendita", "sostituzione", "credito", "incasso")),
        LedgerEntry.status != "annullato", LedgerEntry.counterpart.is_not(None)).distinct()) if (r or "").strip()},
        key=str.lower)
    if names:
        out.append(f"TUTTI I CLIENTI DEL REGISTRO ({len(names)}, nessuno escluso): " + _cap(", ".join(names), 900) + ".")
    out += _self_lines(db, org, settings, now)
    return _cap("\n".join(out), MAX_CHARS)


def _self_lines(db, org: str, settings, now: datetime) -> list[str]:
    """What NODO itself is doing: reading the old chats, what the last night did. Real numbers, so Maria never says
    «the work went on all night» when nothing was read (she did, on 15/10)."""
    from nodo.knowledge.distill import progress
    from nodo.night.briefing import tzinfo
    from nodo.night.selfcare import last_night
    tz = tzinfo(settings.timezone)
    out = []
    try:
        p = progress(db, org)
    except Exception:  # noqa: BLE001 - the shop page must not break for this
        p = None
    if p:
        pct = round(100 * p["read"] / p["total"]) if p["total"] else 0
        last = p["last_at"]
        when = (last if last.tzinfo else last.replace(tzinfo=UTC)).astimezone(tz).strftime("%d/%m alle %H:%M") \
            if last else "mai"
        state = ("in corso adesso" if p["running"] and not p["waiting"] else
                 "in pausa mentre parli con Gaetano, riprende quando c'è silenzio e di notte" if p["running"] else
                 "finito" if not p["left"] else
                 f"fermo ({p['note']})" if p["note"] else "fermo, riparte da solo entro 20 minuti")
        out.append(f"STUDIO DELLE VECCHIE CHAT DI CHATGPT: lette {p['read']} conversazioni su {p['total']} ({pct}%), "
                   f"ne restano {p['left']}; {p['facts']} cose imparate; ultima lettura il {when}; stato: {state}. "
                   "Se te lo chiede, rispondi con QUESTI numeri, mai a sensazione.")
    from nodo.brain.live_work import board_line
    if line := board_line(tz):
        out.append(line)
    try:
        night = last_night(db, org, tz)
    except Exception:  # noqa: BLE001
        night = None
    if night:
        out.append(f"STANOTTE, il tuo turno di notte su te stessa, {night}")
    return out


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
