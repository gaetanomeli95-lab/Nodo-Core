"""The register told like a colleague would tell it: «Ieri a Simone Marrocchino hai venduto un Morfeus matrimoniale,
160 per 190, a 210 euro…». Same numbers as the written recap (computed by nodo/ledger.py), no lists, no symbols,
one customer at a time (Softcomfort's three mattresses and its net are ONE delivery, not two lines)."""
from __future__ import annotations

import re
from datetime import date

from nodo.db.models import LedgerEntry
from nodo.ledger import SIZE_NAMES, say_day

_NUM = ["zero", "un", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove", "dieci"]
_FEM = ("rete", "base", "testiera", "fodera", "trapunta", "coperta", "poltrona", "sedia", "cassapanca")
_PLURAL = {"materasso": "materassi", "rete": "reti", "cuscino": "cuscini", "guanciale": "guanciali",
           "topper": "topper", "letto": "letti", "coprimaterasso": "coprimaterassi", "base": "basi",
           "testiera": "testiere", "fodera": "fodere"}
_WITH_ARTICLE = {"pulizie": "le pulizie", "affitto": "l'affitto", "benzina": "la benzina", "luce": "la luce",
                 "trasporto": "il trasporto", "spedizione": "la spedizione", "consegna": "la consegna"}
_SIZE_PLURAL = {"singolo": "singoli", "singolo grande": "singoli grandi", "matrimoniale": "matrimoniali",
                "matrimoniale grande": "matrimoniali grandi"}


def _money(v: float | None) -> str:
    if v is None:
        return "un prezzo che non mi hai detto"
    s = f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{s} euro"


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " e " + parts[-1] if parts else ""


def _to(name: str) -> str:
    low = name.lower()
    if low.startswith(("signora ", "sig.ra ")):
        return "alla signora " + name.split(" ", 1)[1]
    if low.startswith(("signor ", "sig. ", "signore ")):
        return "al signor " + name.split(" ", 1)[1]
    if low == "te":
        return "a te"
    return ("ad " if low[:1] == "a" else "a ") + name


def _size(size: str | None, plural: bool, fem: bool = False) -> str:
    if not size:
        return ""
    m = re.match(r"(\d+)x(\d+)", size)
    if not m:
        return size
    name = SIZE_NAMES.get(int(m.group(1)), "")
    if plural:
        name = _SIZE_PLURAL.get(name, name)
    if fem:
        name = name.replace("singoli", "singole").replace("singolo", "singola")
    spoken = f"{m.group(1)} per {m.group(2)}"
    return f"{name}, {spoken}" if name else spoken


def _article(item: str, qty: int, size: str | None) -> str:
    """«un materasso Morfeus matrimoniale, 160 per 190» / «tre materassi Arianna matrimoniali, 160 per 190»."""
    words = (item or "articolo").split()
    first = words[0].lower()
    fem = first in _FEM
    if qty > 1:
        words[0] = _PLURAL.get(first, words[0])
        head = (_NUM[qty] if qty <= 10 else str(qty)) + " " + " ".join(words)
    else:
        head = ("una " if fem else ("uno " if first[:1] == "s" and first[1:2] not in "aeiou" else "un ")) + item
    sz = _size(size, qty > 1, fem)
    if sz:
        head += ((" " + sz) if not sz[0].isdigit() else f", {sz}") + ","
    return head


def pronounce(text: str, mapping: dict[str, str] | None) -> str:
    for written, said in (mapping or {}).items():
        text = re.sub(rf"\b{re.escape(written)}\b", said, text, flags=re.I)
    return text


def _sales_for(rows: list[LedgerEntry], owner: str) -> list[str]:
    out = []
    order: list[str] = []
    by: dict[str, list[LedgerEntry]] = {}
    for r in rows:
        key = (r.counterpart or "").strip() or "un cliente"
        if key not in by:
            order.append(key)
        by.setdefault(key, []).append(r)
    for who in order:
        group = by[who]
        done = [r for r in group if r.status == "fatto"]
        todo = [r for r in group if r.status == "da_fare"]
        parts = []
        for r in done:
            each = (f" a {_money(r.unit_price)} l'uno" if r.qty > 1 and r.unit_price is not None
                    else f" a {_money(r.amount)}")
            if r.payment == "pagato" and r.paid_on and r.paid_on != r.day:
                each += ", pagato dopo"
            parts.append(_article(r.item, r.qty, r.size) + each)
        line = ""
        if done:
            verb = "hai consegnato" if len(done) > 1 or any(r.qty > 1 for r in done) else "hai venduto"
            line = f"{_to(who) if who != 'un cliente' else 'a un cliente'} {verb} {_join(parts)}"
            total = sum(r.amount or 0 for r in done)
            unpaid = [r for r in done if r.payment == "sospeso"]
            if len(parts) > 1 or any(r.qty > 1 for r in done):
                line += f": in tutto {_money(total)}"
            if unpaid and len(unpaid) == len(done):
                line += ", che deve ancora pagare" if len(done) > 1 or done[0].qty > 1 else ", ancora da pagare"
            elif unpaid:
                line += f", di cui {_money(sum(r.amount or 0 for r in unpaid))} ancora da pagare"
            out.append(line[0].upper() + line[1:] + ".")
        if todo:
            t = _join([_article(r.item, r.qty, r.size) + f" a {_money(r.amount)}" for r in todo])
            out.append(f"Devi ancora consegnare {_to(who) if who != 'un cliente' else 'a un cliente'} {t}.")
    return out


def _expense(r: LedgerEntry, owner: str) -> str:
    item, who = (r.item or "").strip(), (r.counterpart or "").strip()
    if not item and who and who[:1].islower():          # «palestra», «fornitore Arrisal» saved as a person
        item, who = who, ""
    if who.lower() == owner.lower():
        who = "te"
    money = _money(r.amount)
    if item.lower() == "fornitore" and who and who != "te":
        return f"{money} al fornitore {who}"
    if item.lower().startswith("fornitore"):
        return f"{money} al {item}"
    item = _WITH_ARTICLE.get(item.lower(), item)
    if item and who:
        return f"{money} {_to(who)} per {item}" if item.lower() not in ("stipendio", "paga") else \
            f"{money} di stipendio {_to(who)}"
    if who:
        return f"{money} {_to(who)}"
    return f"{money} di {item}" if item else money


def spoken_recap(rows: list[LedgerEntry], settled: list[LedgerEntry], totals: dict, start: date, end: date,
                 today: date, owner: str = "Gaetano", sounds: dict[str, str] | None = None) -> str:
    if not rows and not settled:
        span = say_day(start, today) if start == end else f"tra {say_day(start, today)} e {say_day(end, today)}"
        return f"Nel registro non c'è ancora niente per {span}."
    out: list[str] = []
    for d in sorted({r.day for r in rows}):
        day_rows = [r for r in rows if r.day == d]
        when = say_day(date.fromisoformat(d), today)
        when = when.split(",")[0] if "," in when else when
        sales = [r for r in day_rows if r.kind == "vendita"]
        swaps = [r for r in day_rows if r.kind == "sostituzione"]
        costs = [r for r in day_rows if r.kind == "spesa"]
        notes = [r for r in day_rows if r.kind == "nota"]
        debts = [r for r in day_rows if r.kind == "credito"]
        lines = _sales_for(sales, owner)
        if lines:
            lines[0] = f"{when.capitalize()}: " + lines[0][0].lower() + lines[0][1:]
        else:
            lines = [f"{when.capitalize()} niente vendite."]
        for r in swaps:
            who = f" {_to(r.counterpart)}" if r.counterpart else ""
            lines.append(f"Hai fatto anche una sostituzione in garanzia{who}: "
                         f"{_article(r.item, r.qty, r.size).rstrip(',')}, senza incasso.")
        if costs:
            tot = sum(r.amount or 0 for r in costs)
            lines.append(f"Sono uscite {_money(tot)}: " + _join([_expense(r, owner) for r in costs]) + "."
                         if len(costs) > 1 else f"È uscita una spesa: {_expense(costs[0], owner)}.")
        for r in debts:
            who = r.counterpart or "qualcuno"
            lines.append(f"{who} ti deve {_money(r.amount)}" + (f" per {r.item}" if r.item else "")
                         + (", li ha già dati." if r.payment == "pagato" else "."))
        for r in notes:
            lines.append((r.note or r.item) + ".")
        out.append(" ".join(lines))
    if settled:
        out.append("Hanno pagato vendite di prima: " + _join(
            [f"{r.counterpart or 'un cliente'} {_money(r.amount)}" for r in settled]) + ".")
    inc, due, uscite, saldo = (totals["incassato"], totals["da_incassare_sospeso"], totals["uscite"],
                               totals["saldo_del_periodo"])
    money = f"In tutto hai incassato {_money(inc)}"
    if due:
        money += f", e ne aspetti ancora {_money(due)}"
    if uscite:
        money += (f". Tolte le uscite, il saldo è di {_money(saldo)}" if saldo >= 0
                  else f". Con le uscite il saldo è in negativo di {_money(-saldo)}")
    out.append(money + ".")
    return pronounce(" ".join(out), sounds)
