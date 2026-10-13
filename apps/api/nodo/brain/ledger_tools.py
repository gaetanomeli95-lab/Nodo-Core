"""Brain tools for the shop register (nodo/ledger.py). Mixed into Toolbox through ExtraTools."""
from __future__ import annotations

from datetime import datetime

from nodo.ledger import KINDS, Ledger, parse_day, sentence, view

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


class LedgerTools:
    """Mixed into Toolbox via ExtraTools: `_register_ledger()` is called from `_register_extra()`."""

    def _ledger(self) -> Ledger:
        return Ledger(self.s, self.org, self.settings.ledger_business, today=datetime.now(self._tz()).date())

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
                e = led.add(**{k: v for k, v in raw.items() if k in _ENTRY["properties"] and v not in (None, "")})
                saved.append(e)
            except (ValueError, TypeError) as ex:
                errors.append(f"{raw.get('item') or raw.get('kind')}: {ex}")
        self.s.flush()
        out = {"saved": len(saved), "rows": [{"id": e.id[:8], "row": sentence(e), "day": e.day} for e in saved],
               "note": "segnato nel registro: per i totali usa ledger_report"}
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
        return {"updated": bool(done), "changes": done, "row": sentence(e), "day": e.day, "entry": view(e)}

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
        rep["note"] = ("Leggi «text» così com'è: contiene tutte le righe con misure, prezzi e clienti e i totali "
                       "calcolati. Non tralasciare righe e non ricalcolare.")
        return rep

    async def ledger_open(self) -> dict:
        led = self._ledger()
        sospesi = led.open_payments()
        todo = led.pending_work()
        return {"pagamenti_sospesi": [{"row": sentence(r), "day": r.day} for r in sospesi],
                "totale_sospeso": round(sum(r.amount or 0 for r in sospesi), 2),
                "da_fare": [{"row": sentence(r), "day": r.day} for r in todo],
                "kinds": list(KINDS)}
