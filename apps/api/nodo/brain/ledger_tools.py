"""Brain tools for the shop register (nodo/ledger.py). Mixed into Toolbox through ExtraTools."""
from __future__ import annotations

from datetime import datetime

from nodo.ledger import (
    KINDS,
    Ledger,
    check_entry,
    day_check,
    due,
    euro,
    parse_day,
    price_history,
    sentence,
    stats,
    view,
)

_STR = {"type": "string"}
_NUM = {"type": "number"}

_ENTRY = {
    "type": "object",
    "properties": {
        "kind": {**_STR, "description": "vendita | sostituzione | spesa | credito (qualcuno deve soldi al negozio, "
                                        "non è una vendita: «Marcello ci deve 30 euro») | nota. Per i soldi che "
                                        "ARRIVANO da un cliente (acconto, saldo di un sospeso) usa ledger_pay"},
        "item": {**_STR, "description": "cosa, col nome COMPLETO del modello: «materasso Morpheus Memory due strati», "
                                        "«rete a doghe», «rete con tavola», per le spese la voce: «palestra», "
                                        "«stipendio», «pulizie», «fornitore Arrisal»"},
        "size": {**_STR, "description": "misura, es. 160x190 (matrimoniale), 80x190 (singolo)"},
        "qty": {"type": "integer", "description": "quanti pezzi (default 1)"},
        "unit_price": {**_NUM, "description": "prezzo di un pezzo in euro"},
        "amount": {**_NUM, "description": "totale della riga in euro (se non dai il prezzo unitario)"},
        "counterpart": {**_STR, "description": "cliente (vendite, sostituzioni) o a chi è andata la spesa"},
        "payment": {**_STR, "description": "pagato (default) | sospeso (non ha ancora pagato) | nessuno"},
        "status": {**_STR, "description": "fatto (default) | da_fare (consegna o lavoro ancora da fare)"},
        "day": {**_STR, "description": "oggi (default), ieri, o una data 12/10"},
        "note": {**_STR, "description": "dettagli: materiale, indirizzo, motivo della sostituzione…"},
    },
    "required": ["kind"],
}


def norm_kind_safe(kind) -> str:
    from nodo.ledger import norm_kind
    try:
        return norm_kind(kind)
    except ValueError:
        return ""


class LedgerTools:
    """Mixed into Toolbox via ExtraTools: `_register_ledger()` is called from `_register_extra()`."""

    def _ledger(self) -> Ledger:
        led = Ledger(self.s, self.org, self.settings.ledger_business, today=datetime.now(self._tz()).date())
        led.owner, led.sounds = self.settings.owner_name, self.settings.pronounce
        return led

    def _register_ledger(self) -> None:
        biz = self.settings.ledger_business
        self._add("ledger_add", f"Segna nel REGISTRO di {biz} una o più righe: vendite, consegne da fare, "
                                "sostituzioni in garanzia, spese/uscite, note. Usalo SEMPRE quando Gaetano racconta "
                                "cosa ha venduto, consegnato, sostituito, speso o cosa deve consegnare: è il "
                                "gestionale, i conti li fa il programma. Una riga per articolo e cliente.",
                  {"type": "object", "properties": {"entries": {"type": "array", "items": {
                      **_ENTRY, "properties": {**_ENTRY["properties"], "again": {
                          "type": "boolean", "description": "true SOLO se Gaetano ha confermato che è un'altra "
                                                            "vendita/spesa uguale a una appena segnata"}}}}},
                   "required": ["entries"]}, self.ledger_add)
        self._add("ledger_update", f"Corregge o completa una riga del registro di {biz} (prezzo, misura, modello, "
                                   "cliente, giorno), la segna consegnata (status fatto), pagata (payment pagato) "
                                   "o annullata (status annullato). Cerca la riga per cliente/articolo.",
                  {"type": "object", "properties": {
                      "entry": {**_STR, "description": "chi o cosa: «Nocera», «Andromeda di oggi», o l'id della riga"},
                      "day": {**_STR, "description": "giorno della riga da cercare, se serve a distinguere"},
                      **{k: v for k, v in _ENTRY["properties"].items() if k != "day"},
                      "new_day": {**_STR, "description": "per spostare la riga a un altro giorno"},
                      "paid_on": {**_STR, "description": "quando ha pagato (default oggi)"}},
                   "required": ["entry"]}, self.ledger_update)
        self._add("ledger_report", f"Riepilogo del registro di {biz} per un giorno o un periodo: ogni vendita, "
                                   "sostituzione e uscita con modello, misura, prezzo e cliente, merce uscita, "
                                   "incassato, sospesi, uscite e saldo CALCOLATI dal programma. Per «riepilogo di "
                                   "ieri/oggi», «quanto ho incassato», «cosa ho consegnato»: leggi il campo text "
                                   "senza tralasciare nulla e senza rifare i conti.",
                  {"type": "object", "properties": {
                      "day": {**_STR, "description": "oggi (default), ieri, 12/10…"},
                      "from_day": {**_STR, "description": "inizio periodo (es. ieri)"},
                      "to_day": {**_STR, "description": "fine periodo (es. oggi)"}}}, self.ledger_report)
        self._add("ledger_prices", f"Listino di {biz} imparato dalle vendite segnate: a quanto è stato venduto un "
                                   "articolo (per misura), le ultime volte e a chi. Per «quanto costa…», «a quanto "
                                   "l'avevo dato…» e per controllare un prezzo che ti sembra strano.",
                  {"type": "object", "properties": {"item": {**_STR, "description": "articolo, anche parziale"},
                                                    "size": _STR}, "required": ["item"]}, self.ledger_prices)
        self._add("ledger_close_day", f"Chiusura della giornata di {biz}: cosa è entrato e uscito, cosa dovrebbe "
                                      "esserci in cassa, consegne in ritardo, chi deve pagare e le righe con qualcosa "
                                      "che non torna (prezzo mancante, misura strana, doppioni). Usalo a fine "
                                      "giornata o quando chiede «chiudiamo la giornata», «quadra tutto?».",
                  {"type": "object", "properties": {
                      "day": {**_STR, "description": "oggi (default) o un altro giorno"},
                      "cash_counted": {**_NUM, "description": "quanto ha contato in cassa, se te lo dice"}}},
                  self.ledger_close_day)
        self._add("ledger_stats", f"{biz} in numeri sugli ultimi giorni (default 30): incassato, uscite, saldo, pezzi "
                                  "venduti, modelli più venduti, clienti migliori, chi deve pagare da più tempo. Per "
                                  "«com'è andato il mese / la settimana?», «cosa vendiamo di più?».",
                  {"type": "object", "properties": {"days": {"type": "integer", "description": "7, 30, 90…"}}},
                  self.ledger_stats)
        self._add("ledger_pay", f"Soldi RICEVUTI da un cliente di {biz}: un acconto, una caparra o il saldo di un "
                                "sospeso («Softcomfort mi ha dato 200 euro», «Rossi ha saldato», «ha lasciato 100 "
                                "di acconto»). Il programma li segna come incasso del giorno e li scala da quello che "
                                "quel cliente deve, dal più vecchio; dice quanto resta. Per una vendita con acconto: "
                                "prima ledger_add con payment sospeso, poi ledger_pay con l'acconto.",
                  {"type": "object", "properties": {
                      "customer": {**_STR, "description": "chi paga"},
                      "amount": {**_NUM, "description": "quanto ha dato, in euro (per «ha saldato» usa quanto "
                                                         "deve: lo trovi in ledger_customer)"},
                      "entry": {**_STR, "description": "la vendita precisa, se lo dice («quello del materasso»)"},
                      "day": {**_STR, "description": "oggi (default) o quando ha pagato"},
                      "note": {**_STR, "description": "contanti, bancomat, bonifico…"}},
                   "required": ["customer", "amount"]}, self.ledger_pay)
        self._add("ledger_customer", f"Scheda di un cliente di {biz}: tutto quello che ha comprato (quando, cosa, "
                                     "misura, prezzo), quanto ha speso, quanto deve ancora, acconti, note e "
                                     "indirizzi. Per «cosa ha preso Rossi?», «quanto mi deve Softcomfort?», «chi è "
                                     "la signora Silvana?».",
                  {"type": "object", "properties": {"name": {**_STR, "description": "nome del cliente, anche parziale"}},
                   "required": ["name"]}, self.ledger_customer)
        self._add("ledger_pdf", f"Il REGISTRO di {biz} in PDF, impaginato dal programma: ogni giorno con il suo "
                                "titolo, ENTRATE a sinistra e USCITE a destra, totali del giorno, riepilogo e chi "
                                "deve pagare; si apre subito sullo schermo. Per «fammi il PDF del registro», «stampami "
                                "la settimana», «mandami il riepilogo in PDF». Non usare create_document per il registro.",
                  {"type": "object", "properties": {
                      "from_day": {**_STR, "description": "primo giorno (es. 12/10, lunedì, ieri; default oggi)"},
                      "to_day": {**_STR, "description": "ultimo giorno (default uguale al primo, o oggi se dice "
                                                        "«da … a oggi»)"}}}, self.ledger_pdf)
        self._add("ledger_open", f"Cosa resta aperto nel registro di {biz}: pagamenti sospesi (chi deve ancora "
                                 "pagare e quanto) e consegne/lavori da fare.", {"type": "object", "properties": {}},
                  self.ledger_open)

    async def ledger_add(self, entries: list[dict]) -> dict:
        if not isinstance(entries, list) or not entries:
            return {"saved": 0, "error": "nessuna riga da segnare"}
        led = self._ledger()
        saved, errors, twins, fixes = [], [], [], []
        mine: set[str] = set()
        for raw in entries[:30]:
            if not isinstance(raw, dict):
                continue
            try:
                clean = {k: v for k, v in raw.items() if k in _ENTRY["properties"] and v not in (None, "")}
                if norm_kind_safe(clean.get("kind")) == "spesa" and not clean.get("item") and clean.get("counterpart"):
                    clean["item"], clean["counterpart"] = clean["counterpart"], None    # «palestra 65»
                if not raw.get("again"):
                    old, exact = led.twin(**clean, skip=mine)
                    if old is not None and exact:
                        twins.append(sentence(old))           # already there: written once, not twice
                        continue
                    if old is not None:
                        fixes.append({"id": old.id[:8], "row": sentence(old), "new": clean})
                        continue
                e = led.add(**clean)
                mine.add(e.id)
                saved.append((e, raw))
            except (ValueError, TypeError) as ex:
                errors.append(f"{raw.get('item') or raw.get('kind')}: {ex}")
        self.s.flush()
        rows = []
        for e, raw in saved:
            warn = check_entry(led, e, explicit_payment=bool(raw.get("payment")))
            rows.append({"id": e.id[:8], "row": sentence(e), "day": e.day, **({"check": warn} if warn else {})})
        saved = [e for e, _ in saved]
        out = {"saved": len(saved), "rows": rows,
               "say": "Ripeti a Gaetano, in breve, le righe così come sono segnate (modello, misura, prezzo, cliente) "
                      "e chiedi «giusto?». Se una riga ha «check», chiediglielo SUBITO, una domanda alla volta, e "
                      "correggi con ledger_update quello che ti dice."}
        hints = {e.item: led.similar_items(e.item) for e in saved if led.similar_items(e.item)}
        if hints:
            out["check_names"] = {k: f"nome breve: era forse uno di {v}? Se sì correggi con ledger_update"
                                  for k, v in hints.items()}
        if twins:
            out["already_there"] = twins
            out["say_twins"] = ("Queste righe erano GIÀ nel registro (segnate pochi minuti fa): non le ho scritte una "
                                "seconda volta. Dillo a Gaetano in breve.")
        if fixes:
            out["maybe_corrections"] = fixes
            out["say_fixes"] = ("Per queste righe c'è già una riga di pochi minuti fa per lo stesso cliente e articolo: "
                                "NON le ho aggiunte. Se Gaetano sta CORREGGENDO (prezzo, pagato/sospeso, acconto) usa "
                                "ledger_update su quella riga (e ledger_pay per un acconto); se è davvero un'altra "
                                "vendita uguale, rifai ledger_add con again true. Nel dubbio chiediglielo.")
        if errors:
            out["errors"] = errors
        return out

    async def ledger_pdf(self, from_day: str | None = None, to_day: str | None = None) -> dict:
        if self.c is None or self.c.files is None:
            return {"error": "archivio file non disponibile"}
        from nodo.ledger import parse_day
        from nodo.ledger_pdf import build, day_title, file_name
        led = self._ledger()
        from datetime import timedelta
        try:
            if from_day in ("week", "settimana"):
                start = led.today - timedelta(days=6)
            elif from_day in ("recent", "recenti"):          # the last days with something written, up to a week
                days = led.report(led.today - timedelta(days=6), led.today)["days_with_entries"]
                start = parse_day(days[0], led.today) if days else led.today
            else:
                start = parse_day(from_day, led.today) if from_day else led.today
            end = parse_day(to_day, led.today) if to_day else start
        except ValueError as e:
            return {"error": str(e)}
        if end < start:
            start, end = end, start
        if (end - start).days > 366:
            return {"error": "periodo troppo lungo: al massimo un anno"}
        try:
            data = build(led, start, end, self.settings.ledger_business or "il negozio")
        except Exception as e:  # noqa: BLE001
            return {"error": f"non sono riuscita a impaginare il PDF: {type(e).__name__}: {str(e)[:150]}"}
        p = self.c.files.save_bytes(file_name(start, end), data)
        label = day_title(start) if start == end else f"dal {day_title(start)} al {day_title(end)}"
        doc = self.c.files.register(self.s, self.org, p.name, f"Registro {label}",
                                    summary=f"registro in PDF ({len(data) // 1024 or 1} KB)")
        return {"created": doc.title, "url": doc.uri, "format": "pdf",
                "note": "PDF pronto e già sullo schermo: giorni separati, entrate a sinistra, uscite a destra."}

    async def ledger_update(self, entry: str, day: str | None = None, new_day: str | None = None,
                            **changes) -> dict:
        led = self._ledger()
        try:
            e, cands = led.find(entry, day=day)
        except ValueError as ex:
            return {"updated": False, "error": str(ex)}
        if e is None:
            return {"updated": False,
                    "error": "più righe simili: chiedi quale" if cands else f"non trovo «{entry}» nel registro",
                    "candidates": [{"id": r.id[:8], "row": sentence(r), "day": r.day} for r in cands]}
        if new_day:
            changes["day"] = new_day
        try:
            done = led.update(e, **{k: v for k, v in changes.items() if k in _ENTRY["properties"] or k == "paid_on"})
        except ValueError as ex:
            return {"updated": False, "error": str(ex)}
        out = {"updated": bool(done), "changes": done, "row": sentence(e), "day": e.day, "entry": view(e),
               "say": "Ripeti la riga corretta com'è adesso e chiedi conferma."}
        if warn := check_entry(led, e):
            out["check"] = [w for w in warn if not w.startswith("non hai detto")]
        return out

    async def ledger_report(self, day: str | None = None, from_day: str | None = None,
                            to_day: str | None = None) -> dict:
        led = self._ledger()
        try:
            if from_day or to_day:
                start = parse_day(from_day or to_day, led.today)
                end = parse_day(to_day or from_day, led.today)
            else:
                start = end = parse_day(day, led.today)
        except ValueError as ex:
            return {"error": str(ex)}
        if start > end:
            start, end = end, start
        rep = led.report(start, end)
        rep["note"] = ("«say» è il racconto già pronto da dire a voce, con tutti i dettagli e i conti giusti: dillo "
                       "così com'è, con calma, come una collega che racconta la giornata. «text» è la versione "
                       "scritta, che compare sullo schermo: non leggerla.")
        return rep

    async def ledger_prices(self, item: str, size: str | None = None) -> dict:
        from rapidfuzz import fuzz

        from nodo.ledger import norm_size, size_label
        led = self._ledger()
        names = [n for n in led.known_items() if fuzz.token_set_ratio(item.lower(), n.lower()) >= 80
                 or item.lower() in n.lower()]
        if not names:
            return {"prices": [], "note": f"nel registro non ho ancora vendite di «{item}»"}
        want = norm_size(size) if size else None
        out = []
        from sqlalchemy import select

        from nodo.db.models import LedgerEntry
        for n in names[:6]:
            sizes = {r for r in self.s.scalars(select(LedgerEntry.size).where(
                LedgerEntry.organization_id == self.org, LedgerEntry.item == n, LedgerEntry.kind == "vendita"))}
            for sz in sorted(x or "" for x in sizes):
                if want and sz != want:
                    continue
                hist = price_history(led, n, sz or None)
                if hist:
                    out.append({"item": n, "size": size_label(sz or None) or "senza misura",
                                "last": euro(hist[0].unit_price),
                                "history": [f"{euro(r.unit_price)} a {r.counterpart or 'cliente'} il {r.day}"
                                            for r in hist]})
        return {"prices": out[:12], "note": "prezzi presi dalle vendite segnate: possono variare per cliente"}

    async def ledger_close_day(self, day: str | None = None, cash_counted: float | None = None) -> dict:
        led = self._ledger()
        try:
            d = parse_day(day, led.today)
        except ValueError as ex:
            return {"error": str(ex)}
        res = day_check(led, d)
        t = res["totals"]
        lines = [f"Incassato {euro(t['incassato'])}, uscite {euro(t['uscite'])}, saldo {euro(t['saldo_del_periodo'])}."]
        if cash_counted is not None:
            diff = round(float(cash_counted) - t["saldo_del_periodo"], 2)
            res["cash_difference"] = diff
            lines.append("In cassa torna esatto." if abs(diff) < 0.5 else
                         f"In cassa hai contato {euro(float(cash_counted))}: rispetto al registro "
                         f"{'ci sono ' + euro(diff) + ' in più' if diff > 0 else 'mancano ' + euro(-diff)}. "
                         "Forse una spesa o un incasso non segnato, o un pagamento con carta.")
        if res["doubts"]:
            lines.append(f"Da controllare: {len(res['doubts'])} " + ("riga." if len(res["doubts"]) == 1 else "righe."))
        if res["late_deliveries"]:
            lines.append("Consegne rimaste indietro: " + "; ".join(res["late_deliveries"]) + ".")
        if res["open_payments"]:
            lines.append(f"Devono ancora pagare: {len(res['open_payments'])}.")
        res["say"] = " ".join(lines)
        res["note"] = "Dì «say», poi chiedi le righe dubbie una alla volta e correggi con ledger_update."
        return res

    async def ledger_stats(self, days: int = 30) -> dict:
        st = stats(self._ledger(), days)
        t = st["totals"]
        best = st["best_items"][0]["item"] if st["best_items"] else None
        say = (f"Negli ultimi {st['days']} giorni hai incassato {euro(t['incassato'])} e speso {euro(t['uscite'])}: "
               f"saldo {euro(t['saldo'])}, {t['pezzi']} pezzi venduti.")
        if best:
            say += f" Il più venduto è {best}."
        if t["da_incassare"]:
            say += f" Aspetti ancora {euro(t['da_incassare'])}."
        st.pop("series")
        return {**st, "say": say, "note": "i grafici sono nella scheda Registro; puoi mostrarla con show_on_screen"}

    async def ledger_pay(self, customer: str, amount: float, entry: str | None = None, day: str | None = None,
                         note: str | None = None) -> dict:
        led = self._ledger()
        try:
            res = led.pay(customer, amount, day=day, entry=entry, note=note)
        except (ValueError, TypeError) as ex:
            return {"saved": False, "error": str(ex)}
        inc, rows = res["incasso"], res["rows"]
        out = {"saved": True, "incasso": sentence(inc), "day": inc.day,
               "covered": [{"row": sentence(r), "day": r.day} for r in rows],
               "still_due": res["still_due"],
               "say": (f"Segnato: {euro(inc.amount)} da {inc.counterpart}. "
                       + ("Ora è tutto saldato." if res["still_due"] <= 0 and rows else
                          f"Deve ancora {euro(res['still_due'])}." if rows else
                          "Non aveva sospesi: li tengo come credito a suo favore, dimmi se va bene."))}
        if res["left_over"] > 0 and rows:
            out["check"] = f"ha dato {euro(res['left_over'])} più di quanto doveva: è un anticipo o un errore?"
        return out

    async def ledger_customer(self, name: str) -> dict:
        led = self._ledger()
        card = led.customer(name)
        if not card["found"]:
            return {"error": f"non trovo «{name}» tra i clienti del registro", "similar": card["similar"]}
        rows = card["rows"]
        return {"customer": card["name"], "spent": card["spent"], "pieces": card["pieces"], "due": card["due"],
                "first": card["first"], "last": card["last"], "notes": card["notes"],
                "history": [{"row": sentence(r), "day": r.day, "kind": r.kind, "amount": r.amount,
                             "payment": r.payment} for r in rows[-25:]],
                "also_similar": card["others"],
                "say": (f"{card['name']}: {card['pieces']} pezzi in tutto, {euro(card['spent'])} di acquisti"
                        + (f", deve ancora {euro(card['due'])}" if card["due"] else ", nessun sospeso") + ".")}

    async def ledger_open(self) -> dict:
        led = self._ledger()
        sospesi = led.open_payments()
        todo = led.pending_work()
        return {"pagamenti_sospesi": [{"row": sentence(r), "day": r.day} for r in sospesi],
                "totale_sospeso": round(sum(due(r) for r in sospesi), 2),
                "da_fare": [{"row": sentence(r), "day": r.day} for r in todo],
                "kinds": list(KINDS)}
