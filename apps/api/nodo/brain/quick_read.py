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
