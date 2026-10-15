"""Exact read-only requests answered from fresh data without a model round.

Ambiguous requests, filters, writes and compound sentences stay with the brain.
"""
from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())


def match_read(text: str) -> tuple[str, dict] | None:
    text = normalize(text)
    text = re.sub(r"^(?:(?:ciao |ehi )?(?:maria|nodo) )", "", text)
    text = re.sub(r"(?: per favore| grazie)$", "", text)
    if re.fullmatch(r"(?:quali sono |mostrami |dimmi |fammi vedere |leggimi |elenca )?(?:tutti )?"
                    r"(?:i )?(?:pagamenti )?sospesi", text) or text in {
                        "chi mi deve pagare", "chi deve ancora pagare", "quanto devo ancora incassare"}:
        return "ledger_open", {}
    if m := re.fullmatch(r"(?:fammi |mostrami |leggimi |dimmi )?(?:il |un )?"
                         r"(?:riepilogo|resoconto)(?: del registro)? (?:di )?(oggi|ieri)", text):
        return "ledger_report", {"day": m.group(1)}
    return None


_PDF = re.compile(r"\bpdf\b")
_SHOP = re.compile(r"registr|riepilog|giorn|entrat|uscit|incass|negozio|materass|conti|cassa|vendit|settiman|sospes")
_DAYNUM = r"(\d{1,2})(?: (?:ottobre|novembre|dicembre|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre))?"


def match_action(text: str, context: str = "") -> tuple[str, dict] | None:
    """Clear orders the brain kept answering with words only («sto creando il PDF…», 15/10, four times in five
    minutes, nothing made): done by the program, at once."""
    t = normalize(text.split("\n(La voce", 1)[0])
    shop = _SHOP.search(t) or (re.search(r"\b(ri\w*|ancora|di nuovo|quel|stesso)\b", t)
                               and re.search(r"pdf", normalize(context)) and _SHOP.search(normalize(context)))
    if _PDF.search(t) and shop and not re.search(r"\b(non|annulla|lascia stare)\b", t):
        args: dict = {}
        if m := re.search(rf"\bdal(?: giorno)? {_DAYNUM}(?: (?:al|fino al)(?: giorno)? (?:{_DAYNUM}|oggi))?", t):
            args["from_day"] = m.group(1)
            if m.group(2):
                args["to_day"] = m.group(2)
            else:
                args["to_day"] = "oggi"
        elif m := re.search(rf"\b(?:di|del|giorno) (?:giorno )?{_DAYNUM}\b", t):
            args["from_day"] = m.group(1)
        elif "settiman" in t:
            args = {"from_day": "week", "to_day": "oggi"}
        elif re.search(r"\bieri\b", t) and not re.search(r"\boggi\b", t):
            args["from_day"] = "ieri"
        elif re.search(r"\bieri\b", t):
            args = {"from_day": "ieri", "to_day": "oggi"}
        elif re.search(r"\boggi\b", t):
            args["from_day"] = "oggi"
        else:
            args = {"from_day": "recent", "to_day": "oggi"}   # «rifammi il pdf»: the last days with movements
        return "ledger_pdf", args
    if m := re.search(r"\bultim[io] (\d{1,3}) minuti\b", t):
        if re.search(r"conversazion|detto|dett[io]|parlat|trascri|scritt", t):
            return "show_transcript", {"minutes": int(m.group(1))}
    return None


def action_answer(tool: str, result: dict) -> str | None:
    if "error" in result:
        return None
    if tool == "ledger_pdf":
        return f"Ecco il {result.get('created', 'PDF del registro')}: è sullo schermo."
    if tool == "show_transcript":
        return f"Ecco la conversazione, {result.get('lines', 0)} battute: è sullo schermo."
    return None


def answer(tool: str, result: dict) -> str | None:
    if "error" in result:
        return None
    if tool == "ledger_report":
        return result.get("say") or None
    if tool == "ledger_open":
        from nodo.ledger import euro
        rows = result.get("pagamenti_sospesi", [])
        if not rows:
            return "Nel registro non risultano pagamenti sospesi."
        lines = [str(r["row"]).rstrip(".") + "." for r in rows]
        return " ".join(lines) + f" Il totale segnato da incassare è {euro(result['totale_sospeso'])}."
    return None
