"""Brain tools for the shop register (nodo/ledger.py). Mixed into Toolbox through ExtraTools."""
from __future__ import annotations

from datetime import datetime

from nodo.ledger import KINDS, Ledger, check_entry, day_check, euro, parse_day, price_history, sentence, stats, view

_STR = {"type": "string"}
_NUM = {"type": "number"}

_ENTRY = {
    "type": "object",
    "properties": {
        "kind": {**_STR, "description": "vendita | sostituzione | spesa | nota"},
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
                  {"type": "object", "properties": {"entries": {"type": "array", "items": _ENTRY}},
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
        self._add("ledger_open", f"Cosa resta aperto nel registro di {biz}: pagamenti sospesi (chi deve ancora "
                                 "pagare e quanto) e consegne/lavori da fare.", {"type": "object", "properties": {}},
                  self.ledger_open)

    async def ledger_add(self, entries: list[dict]) -> dict:
        if not isinstance(entries, list) or not entries:
            return {"saved": 0, "error": "nessuna riga da segnare"}
        led = self._ledger()
        saved, errors = [], []
        for raw in entries[:30]:
            if not isinstance(raw, dict):
                continue
            try:
                clean = {k: v for k, v in raw.items() if k in _ENTRY["properties"] and v not in (None, "")}
                if norm_kind_safe(clean.get("kind")) == "spesa" and not clean.get("item") and clean.get("counterpart"):
                    clean["item"], clean["counterpart"] = clean["counterpart"], None    # «palestra 65»
                e = led.add(**clean)
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
        if errors:
            out["errors"] = errors
        return out

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

    async def ledger_open(self) -> dict:
        led = self._ledger()
        sospesi = led.open_payments()
        todo = led.pending_work()
        return {"pagamenti_sospesi": [{"row": sentence(r), "day": r.day} for r in sospesi],
                "totale_sospeso": round(sum(r.amount or 0 for r in sospesi), 2),
                "da_fare": [{"row": sentence(r), "day": r.day} for r in todo],
                "kinds": list(KINDS)}
