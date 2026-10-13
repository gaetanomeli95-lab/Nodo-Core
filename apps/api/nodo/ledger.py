"""The shop register: what was sold, delivered, replaced, spent and still to be paid - day by day.

Why it exists: a model asked to "remember" sales as loose notes forgets rows, mixes days and adds badly. Here every
fact is a row with a day, a model, a size, a price, a customer and a payment state; totals and recaps are computed
by this code, always the same way, so «riepilogo dettagliato di ieri» is complete and the sums are right.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nodo.db.models import LedgerEntry

KINDS = ("vendita", "sostituzione", "spesa", "credito", "nota")
MONEY_IN = ("vendita", "credito")      # money someone owes the shop: a sale, or a plain debt («Marcello ci deve 30»)
_KIND_ALIASES = {"venduto": "vendita", "vendite": "vendita", "consegna": "vendita", "ordine": "vendita",
                 "cambio": "sostituzione", "garanzia": "sostituzione", "sostituito": "sostituzione",
                 "uscita": "spesa", "uscite": "spesa", "pagamento": "spesa", "stipendio": "spesa",
                 "appunto": "nota", "debito": "credito", "prestito": "credito", "deve": "credito",
                 "crediti": "credito"}
SIZE_NAMES = {80: "singolo", 85: "singolo", 90: "singolo", 100: "singolo grande", 120: "una piazza e mezzo",
              140: "alla francese", 160: "matrimoniale", 170: "matrimoniale", 180: "matrimoniale grande"}
_WORD_SIZES = {"singolo": "80x190", "singola": "80x190", "una piazza e mezzo": "120x190",
               "piazza e mezza": "120x190", "francese": "140x190", "alla francese": "140x190",
               "matrimoniale": "160x190", "matrimoniali": "160x190"}
_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"]
_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


# ------------------------------------------------------------------------------------------------ normalizers
def norm_kind(kind: str | None) -> str:
    k = (kind or "").strip().lower()
    k = _KIND_ALIASES.get(k, k)
    if k not in KINDS:
        raise ValueError(f"tipo non valido «{kind}»: usa vendita, sostituzione, spesa, credito o nota")
    return k


def norm_size(text: str | None) -> str | None:
    """«160 x 190», «160 per 190», «160x90» (190 detto in fretta), «matrimoniale» -> «160x190»."""
    if not text:
        return None
    t = str(text).strip().lower()
    m = re.search(r"(\d{2,3})\s*(?:x|×|\*|per)\s*(\d{2,3})", t)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if h in (90, 95) and w >= 80:          # «80x90», «160x90»: the length of a bed is 190/195/200, not 90
            h += 100
        return f"{w}x{h}"
    for word, size in sorted(_WORD_SIZES.items(), key=lambda kv: -len(kv[0])):
        if word in t:
            return size
    return str(text).strip()[:40]


def size_label(size: str | None) -> str:
    if not size:
        return ""
    m = re.match(r"(\d+)x(\d+)", size)
    name = SIZE_NAMES.get(int(m.group(1))) if m else None
    return f"{size} ({name})" if name else size


def parse_day(text: str | None, today: date) -> date:
    """oggi / ieri / l'altro ieri / domani / 2026-10-12 / 12/10 / 12 ottobre / lunedì (the last one)."""
    if not text:
        return today
    t = str(text).strip().lower()
    if t in ("oggi", "today"):
        return today
    if t in ("ieri", "yesterday"):
        return today - timedelta(days=1)
    if t in ("l'altro ieri", "altro ieri", "ieri l'altro"):
        return today - timedelta(days=2)
    if t == "domani":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
    if m:
        y = int(m.group(3)) if m.group(3) else today.year
        return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(?:\w+ )?(\d{1,2}) (\w+)(?: (\d{4}))?", t)
    if m and m.group(2) in _MONTHS:
        return date(int(m.group(3) or today.year), _MONTHS.index(m.group(2)) + 1, int(m.group(1)))
    for i, name in enumerate(_DAYS):
        if t.startswith(name):
            back = (today.weekday() - i) % 7 or 7
            return today - timedelta(days=back)
    raise ValueError(f"non capisco il giorno «{text}»: usa oggi, ieri o una data come 12/10")


def say_day(d: date, today: date) -> str:
    rel = {0: "oggi", 1: "ieri", -1: "domani"}.get((today - d).days)
    full = f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"
    return f"{rel}, {full}" if rel else full


def euro(v: float | None) -> str:
    if v is None:
        return "prezzo non indicato"
    return f"{v:.2f}".replace(".", ",").removesuffix(",00") + " euro"


# ------------------------------------------------------------------------------------------------ service
@dataclass
class Totals:
    incassato: float = 0.0          # sales paid that day + earlier sales settled that day
    da_incassare: float = 0.0       # sales of the period still "sospeso"
    uscite: float = 0.0

    @property
    def saldo(self) -> float:
        return self.incassato - self.uscite


class Ledger:
    def __init__(self, s: Session, org: str, business: str = "", today: date | None = None):
        self.s, self.org, self.business = s, org, business
        self.today = today or date.today()

    # -- names: same spelling as before when only the case differs. A short name is NOT completed automatically
    # («Andromeda» could be H25 or another one): the brain is told to use the full model name instead.
    def known_items(self) -> list[str]:
        rows = self.s.scalars(select(LedgerEntry.item).where(LedgerEntry.organization_id == self.org)
                              .distinct()).all()
        return [r for r in rows if r]

    def canonical_item(self, item: str) -> str:
        item = re.sub(r"\s+", " ", (item or "").strip())
        same = [k for k in self.known_items() if k.lower() == item.lower()]
        return same[0] if same else item

    def similar_items(self, item: str) -> list[str]:
        """Longer names already used that contain every word of `item` (to suggest, never to replace)."""
        words = set((item or "").lower().split())
        return [k for k in self.known_items() if words and words < set(k.lower().split())][:4]

    # -- write
    def add(self, *, kind: str, item: str = "", size: str | None = None, qty: int | None = 1,
            unit_price: float | None = None, amount: float | None = None, counterpart: str | None = None,
            payment: str | None = None, status: str | None = None, day: str | None = None,
            note: str | None = None, at: datetime | None = None) -> LedgerEntry:
        k = norm_kind(kind)
        qty = max(1, int(qty or 1))
        if amount is None and unit_price is not None:
            amount = round(float(unit_price) * qty, 2)
        if unit_price is None and amount is not None and qty:
            unit_price = round(float(amount) / qty, 2)
        pay = (payment or ("pagato" if k in ("vendita", "spesa") else "sospeso" if k == "credito" else "nessuno")).lower()
        if pay not in ("pagato", "sospeso", "nessuno"):
            raise ValueError("pagamento: pagato, sospeso o nessuno")
        st = (status or "fatto").lower().replace(" ", "_").replace("da fare", "da_fare")
        if st not in ("da_fare", "fatto", "annullato"):
            raise ValueError("stato: da_fare, fatto o annullato")
        d = parse_day(day, self.today)
        e = LedgerEntry(organization_id=self.org, business=self.business, day=d.isoformat(), kind=k,
                        item=self.canonical_item(item), size=norm_size(size), qty=qty,
                        unit_price=float(unit_price) if unit_price is not None else None,
                        amount=float(amount) if amount is not None else None,
                        counterpart=(counterpart or "").strip() or None, payment=pay,
                        paid_on=d.isoformat() if pay == "pagato" and k in MONEY_IN else None,
                        status=st, note=(note or "").strip() or None)
        if at is not None:
            e.at = at
        self.s.add(e)
        self.s.flush()
        return e

    def find(self, query: str, day: str | None = None, kind: str | None = None) -> tuple[LedgerEntry | None, list]:
        q = select(LedgerEntry).where(LedgerEntry.organization_id == self.org, LedgerEntry.status != "annullato")
        if day:
            q = q.where(LedgerEntry.day == parse_day(day, self.today).isoformat())
        if kind:
            q = q.where(LedgerEntry.kind == norm_kind(kind))
        rows = list(self.s.scalars(q.order_by(LedgerEntry.day.desc(), LedgerEntry.at.desc()).limit(200)))
        exact = [r for r in rows if r.id == query or r.id.startswith(query)]
        if exact:
            return exact[0], []
        text = (query or "").lower()

        def score(r: LedgerEntry) -> float:
            hay = f"{r.counterpart or ''} {r.item} {r.size or ''} {r.note or ''}".lower()
            return max(fuzz.token_set_ratio(text, hay), fuzz.partial_ratio(text, hay) if len(text) >= 4 else 0)
        scored = sorted(((score(r), r) for r in rows), key=lambda x: -x[0])
        good = [r for sc, r in scored if sc >= 75]
        if not good:
            return None, []
        top = [r for sc, r in scored if sc >= scored[0][0] - 3 and sc >= 75]
        if len(top) == 1 or len({r.day for r in top}) == 1 and len(top) == 1:
            return top[0], []
        newest_day = max(r.day for r in top)
        newest = [r for r in top if r.day == newest_day]
        if len(newest) == 1:                 # «quello di Nocera» = the most recent one, unless the day says otherwise
            return newest[0], []
        return None, top[:6]

    def update(self, e: LedgerEntry, **changes) -> list[str]:
        """All or nothing: every field is checked first; one invalid value changes NOTHING (no half-saved row
        behind a «non aggiornato»)."""
        new: dict = {}
        paid_on: str | None | bool = False     # False = untouched
        for key in ("item", "size", "qty", "unit_price", "amount", "counterpart", "payment", "status", "day",
                    "note", "kind"):
            if key not in changes or changes[key] in (None, ""):
                continue
            v = changes[key]
            if key == "size":
                v = norm_size(v)
            elif key == "item":
                v = self.canonical_item(v)
            elif key == "kind":
                v = norm_kind(v)
            elif key == "day":
                v = parse_day(v, self.today).isoformat()
            elif key in ("unit_price", "amount"):
                try:
                    v = float(str(v).replace("€", "").replace(",", ".").strip())
                except ValueError:
                    raise ValueError(f"{key}: «{v}» non è un numero") from None
                if v < 0:
                    raise ValueError(f"{key}: non può essere negativo")
            elif key == "qty":
                try:
                    v = max(1, int(float(str(v).replace(",", "."))))
                except ValueError:
                    raise ValueError(f"quantità: «{v}» non è un numero") from None
            elif key == "status":
                v = str(v).lower().replace(" ", "_")
                if v not in ("da_fare", "fatto", "annullato"):
                    raise ValueError("stato: da_fare, fatto o annullato")
            elif key == "payment":
                v = str(v).lower()
                if v not in ("pagato", "sospeso", "nessuno"):
                    raise ValueError("pagamento: pagato, sospeso o nessuno")
                if v == "pagato" and e.payment != "pagato":
                    paid_on = parse_day(changes.get("paid_on"), self.today).isoformat()
                elif v != "pagato":
                    paid_on = None
            new[key] = v
        # every value is valid: only now touch the row
        done: list[str] = []
        if paid_on is not False:
            e.paid_on = paid_on
        for key, v in new.items():
            old = getattr(e, key)
            if old != v:
                setattr(e, key, v)
                done.append(f"{key}: {old if old is not None else '—'} → {v}")
        if any(c.startswith(("unit_price", "qty")) for c in done) and "amount" not in changes \
                and e.unit_price is not None:
            e.amount = round(e.unit_price * e.qty, 2)
        elif any(c.startswith("amount") for c in done) and "unit_price" not in changes and e.qty:
            e.unit_price = round((e.amount or 0) / e.qty, 2)
        self.s.flush()
        return done

    # -- read
    def entries(self, start: date, end: date, include_cancelled: bool = False) -> list[LedgerEntry]:
        q = select(LedgerEntry).where(LedgerEntry.organization_id == self.org,
                                      LedgerEntry.day >= start.isoformat(), LedgerEntry.day <= end.isoformat())
        if not include_cancelled:
            q = q.where(LedgerEntry.status != "annullato")
        return list(self.s.scalars(q.order_by(LedgerEntry.day, LedgerEntry.at)))

    def settled_in(self, start: date, end: date) -> list[LedgerEntry]:
        """Earlier sales paid within the period (a «sospeso» settled later)."""
        return list(self.s.scalars(select(LedgerEntry).where(
            LedgerEntry.organization_id == self.org, LedgerEntry.kind.in_(MONEY_IN),
            LedgerEntry.status != "annullato", LedgerEntry.payment == "pagato",
            LedgerEntry.paid_on >= start.isoformat(), LedgerEntry.paid_on <= end.isoformat(),
            LedgerEntry.day < start.isoformat())))

    def open_payments(self) -> list[LedgerEntry]:
        return list(self.s.scalars(select(LedgerEntry).where(
            LedgerEntry.organization_id == self.org, LedgerEntry.kind.in_(MONEY_IN),
            LedgerEntry.status == "fatto", LedgerEntry.payment == "sospeso").order_by(LedgerEntry.day)))

    def pending_work(self) -> list[LedgerEntry]:
        return list(self.s.scalars(select(LedgerEntry).where(
            LedgerEntry.organization_id == self.org, LedgerEntry.status == "da_fare",
            or_(LedgerEntry.day <= (self.today + timedelta(days=7)).isoformat()))
            .order_by(LedgerEntry.day, LedgerEntry.at)))

    def report(self, start: date, end: date) -> dict:
        rows = self.entries(start, end)
        settled = self.settled_in(start, end)
        t = Totals()
        for r in rows:
            if r.kind in MONEY_IN and r.status != "da_fare":
                if r.payment == "pagato" and (r.paid_on or r.day) <= end.isoformat():
                    t.incassato += r.amount or 0
                elif r.payment in ("sospeso", "pagato"):        # paid only after the period: still open then
                    t.da_incassare += r.amount or 0
            elif r.kind == "spesa" and r.status != "da_fare":
                t.uscite += r.amount or 0
        t.incassato += sum(r.amount or 0 for r in settled)
        goods: dict[tuple[str, str], int] = {}
        for r in rows:
            if r.kind in ("vendita", "sostituzione") and r.status == "fatto" and r.item:
                key = (r.item, size_label(r.size))
                goods[key] = goods.get(key, 0) + r.qty
        days = sorted({r.day for r in rows})
        return {
            "from": start.isoformat(), "to": end.isoformat(),
            "entries": [view(r) for r in rows],
            "settled_earlier_sales": [view(r) for r in settled],
            "totals": {"incassato": round(t.incassato, 2), "da_incassare_sospeso": round(t.da_incassare, 2),
                       "uscite": round(t.uscite, 2), "saldo_del_periodo": round(t.saldo, 2)},
            "goods_out": [{"item": i, "size": s, "pieces": n} for (i, s), n in sorted(goods.items())],
            "days_with_entries": days,
            "text": self.recap_text(start, end, rows, settled, t),
            "say": self.spoken(start, end, rows, settled, t),
        }

    owner: str = "Gaetano"
    sounds: dict | None = None

    def spoken(self, start: date, end: date, rows: list[LedgerEntry], settled: list[LedgerEntry],
               t: Totals) -> str:
        from nodo.ledger_voice import spoken_recap
        totals = {"incassato": round(t.incassato, 2), "da_incassare_sospeso": round(t.da_incassare, 2),
                  "uscite": round(t.uscite, 2), "saldo_del_periodo": round(t.saldo, 2)}
        return spoken_recap(rows, settled, totals, start, end, self.today, self.owner, self.sounds)

    def recap_text(self, start: date, end: date, rows: list[LedgerEntry], settled: list[LedgerEntry],
                   t: Totals) -> str:
        """The detailed recap exactly as Gaetano wants it: model, size, price, customer for every row."""
        if not rows and not settled:
            return f"Nel registro non c'è niente per {self._span(start, end)}."
        out: list[str] = []
        for d in sorted({r.day for r in rows}):
            day_rows = [r for r in rows if r.day == d]
            out.append(f"{say_day(date.fromisoformat(d), self.today).capitalize()}:")
            for kind, title in (("vendita", "Vendite"), ("sostituzione", "Sostituzioni"), ("spesa", "Uscite"),
                                ("credito", "Crediti"),
                                ("nota", "Note")):
                part = [r for r in day_rows if r.kind == kind]
                if part:
                    out.append(f"{title}: " + "; ".join(sentence(r) for r in part) + ".")
        if settled:
            out.append("Incassi di vendite precedenti: " + "; ".join(sentence(r) for r in settled) + ".")
        money = [f"incassato {euro(t.incassato)}", f"uscite {euro(t.uscite)}", f"saldo {euro(t.saldo)}"]
        if t.da_incassare:
            money.insert(1, f"da incassare (sospeso) {euro(t.da_incassare)}")
        out.append("Totali: " + ", ".join(money) + ".")
        return "\n".join(out)

    def _span(self, start: date, end: date) -> str:
        return say_day(start, self.today) if start == end else \
            f"dal {say_day(start, self.today)} al {say_day(end, self.today)}"


def _to(name: str) -> str:
    return ("ad " if name[:1].lower() == "a" else "a ") + name


def sentence(r: LedgerEntry) -> str:
    what = f"{r.qty} × " if r.qty > 1 else ""
    what += r.item or ("" if r.kind == "spesa" else "(articolo non indicato)")
    if r.size:
        what += f" {size_label(r.size)}"
    if r.kind == "spesa" and not r.item and r.counterpart and r.counterpart[:1].islower():
        what = r.counterpart                     # «palestra», «fornitore Arrisal» were saved as the receiver
        if r.size:
            what += f" {size_label(r.size)}"
        return f"{what} {euro(r.amount)}"
    if r.kind == "spesa":
        who = r.counterpart if r.counterpart and r.counterpart.lower() not in what.lower() else None
        s = f"{what} {euro(r.amount)}".strip() + (f" {_to(who)}" if who else "")
    elif r.kind == "sostituzione":
        s = f"{what}" + (f" per {r.counterpart}" if r.counterpart else "") + " (in garanzia, nessun incasso)"
    elif r.kind == "credito":
        s = f"{r.counterpart or 'qualcuno'} deve {euro(r.amount)}" + (f" ({r.item})" if r.item else "")
        s += {"pagato": ", pagato", "sospeso": "", "nessuno": ""}.get(r.payment, "")
        return s + (f" ({r.note})" if r.note else "")
    elif r.kind == "nota":
        s = r.note or r.item
        return s + (" [da fare]" if r.status == "da_fare" else "")
    else:
        each = f" a {euro(r.unit_price)} l'uno, totale {euro(r.amount)}" if r.qty > 1 and r.unit_price else \
            f" a {euro(r.amount)}"
        s = f"{what}{each}" + (f" {_to(r.counterpart)}" if r.counterpart else "")
        s += {"pagato": ", pagato", "sospeso": ", pagamento SOSPESO", "nessuno": ""}.get(r.payment, "")
        if r.payment == "pagato" and r.paid_on and r.paid_on != r.day:
            s += f" il {date.fromisoformat(r.paid_on).day}/{date.fromisoformat(r.paid_on).month}"
    if r.status == "da_fare":
        s += " [da consegnare/da fare]"
    if r.note and r.kind != "nota":
        s += f" ({r.note})"
    return s


def view(r: LedgerEntry) -> dict:
    return {"id": r.id[:8], "day": r.day, "kind": r.kind, "item": r.item, "size": size_label(r.size), "qty": r.qty,
            "unit_price": r.unit_price, "amount": r.amount, "counterpart": r.counterpart, "payment": r.payment,
            "paid_on": r.paid_on, "status": r.status, "note": r.note}


# ------------------------------------------------------------------------------------------------ the second pair of eyes
_STD_W = {80, 85, 90, 100, 120, 140, 160, 170, 180, 190, 200}
_STD_L = {190, 195, 200, 210}
_NEEDS_SIZE = re.compile(r"materass|rete|topper|guanciale|cuscino|letto|sommier", re.I)


def price_history(led: Ledger, item: str, size: str | None, exclude_id: str | None = None,
                  limit: int = 5) -> list[LedgerEntry]:
    """Previous sales of the same article and size (newest first): the price list learns by itself."""
    q = select(LedgerEntry).where(LedgerEntry.organization_id == led.org, LedgerEntry.kind == "vendita",
                                  LedgerEntry.status != "annullato", LedgerEntry.unit_price.is_not(None))
    rows = [r for r in led.s.scalars(q.order_by(LedgerEntry.day.desc(), LedgerEntry.at.desc()).limit(400))
            if r.id != exclude_id and r.item.lower() == (item or "").lower() and (r.size or "") == (size or "")]
    return rows[:limit]


def check_entry(led: Ledger, e: LedgerEntry, *, explicit_payment: bool = True) -> list[str]:
    """Doubts about one row, in plain Italian, to ask Gaetano BEFORE moving on. Empty = nothing odd."""
    w: list[str] = []
    if e.status == "annullato":
        return w
    if e.kind in ("vendita", "spesa", "credito") and e.amount is None:
        w.append("manca il prezzo")
    if e.kind in ("vendita", "sostituzione") and _NEEDS_SIZE.search(e.item or "") and not e.size:
        w.append("manca la misura")
    if e.kind == "vendita" and not e.counterpart:
        w.append("manca il nome del cliente")
    if e.kind == "vendita" and not explicit_payment and e.status == "fatto":
        w.append("non hai detto se ha pagato: l'ho segnato come pagato")
    if e.size:
        m = re.fullmatch(r"(\d+)x(\d+)", e.size)
        if not m or int(m.group(1)) not in _STD_W or int(m.group(2)) not in _STD_L:
            w.append(f"misura insolita {e.size}: è giusta?")
    if e.qty > 8:
        w.append(f"{e.qty} pezzi: è giusto?")
    if not e.item and e.kind not in ("nota", "credito"):
        w.append("manca cosa (l'articolo o la voce di spesa)")
    if e.kind == "vendita" and e.unit_price is not None:
        hist = price_history(led, e.item, e.size, exclude_id=e.id)
        if hist:
            last = hist[0]
            diff = abs((e.unit_price or 0) - (last.unit_price or 0))
            if diff >= 5 and diff >= 0.08 * max(last.unit_price or 0, 1):
                when = date.fromisoformat(last.day)
                w.append(f"l'ultima volta {e.item} {size_label(e.size)} era {euro(last.unit_price)} "
                         f"({last.counterpart or 'cliente'}, {when.day}/{when.month}): ora {euro(e.unit_price)}, "
                         "confermi?".replace("  ", " "))
    dupes = [r for r in led.entries(date.fromisoformat(e.day), date.fromisoformat(e.day))
             if r.id != e.id and r.kind == e.kind and r.item.lower() == (e.item or "").lower()
             and (r.size or "") == (e.size or "") and (r.counterpart or "").lower() == (e.counterpart or "").lower()]
    if dupes:
        w.append("c'è già una riga uguale nello stesso giorno: è un doppione o sono due pezzi?")
    if e.status == "fatto" and e.day > led.today.isoformat():
        w.append("è un giorno futuro ma risulta già fatto")
    if e.kind == "vendita" and e.payment == "sospeso" and e.status == "da_fare":
        pass                                    # normal: paid on delivery
    return w


def day_check(led: Ledger, day: date) -> dict:
    """End-of-day closing: money, what is still open, and every doubtful row."""
    rep = led.report(day, day)
    doubts = []
    for r in led.entries(day, day):
        for w in check_entry(led, r):
            if not w.startswith("non hai detto"):
                doubts.append(f"{sentence(r)}: {w}")
    late = [r for r in led.pending_work() if r.day < day.isoformat()]
    return {"day": day.isoformat(), "totals": rep["totals"], "text": rep["text"], "doubts": doubts,
            "late_deliveries": [f"{sentence(r)} (era per il {date.fromisoformat(r.day).day}/{date.fromisoformat(r.day).month})"
                                for r in late],
            "open_payments": [sentence(r) for r in led.open_payments()]}


_SALE_NOTE = re.compile(r"\d+[.,]?\d*\s*(?:€|euro)|\bvendut|\bvenduto|\bvendita\b|\bconsegn|\bsostituit|\bsostituzione"
                        r"|\bincass|\bpagamento\b.*\bsospes", re.I)
_STALE_NOTE = re.compile(r"\b\d{1,2} (?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|"
                         r"novembre|dicembre) 20\d\d\b|\bpartito dal negozio\b|completi e corretti|si è accordato per "
                         r"sostituire|non può creare (?:file|pdf)|\brinominat|nome dell'assistente|\bil tuo nome è\b",
                         re.I)
_RULE_NOTE = re.compile(r"\bquando\b|\bsempre\b|\bogni volta\b|\bpreferisc|\bvuole che\b|\bvoglio che\b|\bdevi\b|"
                        r"\bregola\b|\bmisure standard\b", re.I)


def archive_shop_notes(db: Session) -> int:
    """Before the register, sales and prices were saved as loose memory notes - with the errors Gaetano corrected.
    They now contradict the register: archive them (not deleted, still visible as history). Also archived: dated
    one-off events, old names of the assistant (the name is a setting now) and «non può creare PDF» (it can).
    Rules such as «quando chiedo un riepilogo dimmi le misure» are kept."""
    from nodo.db.models import FactStatus, MemoryFact
    n = 0
    for f in db.scalars(select(MemoryFact).where(MemoryFact.source_type == "conversation",
                                                 MemoryFact.status.in_((FactStatus.CONFIRMED, FactStatus.INFERRED,
                                                                        FactStatus.UNVERIFIED)),
                                                 MemoryFact.valid_until.is_(None))):
        text = f.content or ""
        stale = _STALE_NOTE.search(text) and not re.search(r"\bvuole che\b|\bpreferisc", text, re.I)
        if stale or (_SALE_NOTE.search(text) and not _RULE_NOTE.search(text)):
            f.status = FactStatus.ARCHIVED
            f.valid_until = datetime.now(UTC)
            n += 1
    db.flush()
    return n


def stats(led: Ledger, days: int = 30) -> dict:
    """The shop in numbers over the last `days`: money per day, best sellers, best customers, open credits."""
    days = max(7, min(int(days or 30), 366))
    start, end = led.today - timedelta(days=days - 1), led.today
    rows = led.entries(start, end)
    per_day: dict[str, dict] = {(start + timedelta(days=i)).isoformat(): {"in": 0.0, "out": 0.0, "pieces": 0}
                                for i in range(days)}
    for r in rows:
        d = per_day.get(r.day)
        if d is None or r.status == "da_fare":
            continue
        if r.kind in MONEY_IN:
            d["pieces"] += r.qty if r.kind == "vendita" else 0
            if r.payment == "pagato":
                d["in"] += r.amount or 0
        elif r.kind == "spesa":
            d["out"] += r.amount or 0
    for r in led.settled_in(start, end):           # older sales paid in the period count the day they were paid
        if r.paid_on in per_day:
            per_day[r.paid_on]["in"] += r.amount or 0
    items: dict[str, dict] = {}
    customers: dict[str, float] = {}
    for r in rows:
        if r.kind != "vendita" or r.status == "da_fare":
            continue
        key = f"{r.item} {size_label(r.size)}".strip()
        it = items.setdefault(key, {"item": key, "pieces": 0, "revenue": 0.0})
        it["pieces"] += r.qty
        it["revenue"] += r.amount or 0
        if r.counterpart:
            customers[r.counterpart] = customers.get(r.counterpart, 0) + (r.amount or 0)
    tot_in = sum(d["in"] for d in per_day.values())
    tot_out = sum(d["out"] for d in per_day.values())
    open_pay = led.open_payments()
    return {"from": start.isoformat(), "to": end.isoformat(), "days": days,
            "series": [{"day": k, "in": round(v["in"], 2), "out": round(v["out"], 2), "pieces": v["pieces"]}
                       for k, v in per_day.items()],
            "totals": {"incassato": round(tot_in, 2), "uscite": round(tot_out, 2), "saldo": round(tot_in - tot_out, 2),
                       "pezzi": sum(v["pieces"] for v in per_day.values()),
                       "da_incassare": round(sum(r.amount or 0 for r in open_pay), 2)},
            "best_items": sorted(items.values(), key=lambda x: (-x["pieces"], -x["revenue"]))[:6],
            "best_customers": [{"name": k, "revenue": round(v, 2)}
                               for k, v in sorted(customers.items(), key=lambda kv: -kv[1])[:6]],
            "oldest_open": [{"who": r.counterpart or "cliente", "amount": r.amount, "since": r.day}
                            for r in open_pay[:5]]}
