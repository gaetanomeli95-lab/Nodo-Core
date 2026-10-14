"""What to put «on the table» (the card in the middle of the panel) after a tool ran: same for text and voice."""
from __future__ import annotations

FILE_TOOLS = ("create_file", "generate_image", "create_document")


def surface_for(name: str, result: dict) -> dict | None:
    """-> {"view", "rows", "summary"} or None. Rows carry only what the card shows."""
    if not isinstance(result, dict) or "error" in result:
        return None
    if name == "show_on_screen":
        if result.get("tab"):
            return {"view": "tab", "rows": [{"tab": result["tab"]}], "summary": ""}
        return {"view": "viewer", "rows": [{"title": result.get("shown"), "url": result.get("url"),
                                            "kind": result.get("kind")}], "summary": ""}
    if name in ("board_pin", "board_done"):
        return {"view": "board", "rows": [], "summary": ""}
    if name == "close_screen":
        return {"view": "close", "rows": [], "summary": ""}
    if name in FILE_TOOLS and result.get("url"):
        url = result["url"]
        ext = url.rsplit(".", 1)[-1].lower()
        kind = {"png": "image", "jpg": "image", "jpeg": "image", "webp": "image", "pdf": "pdf", "docx": "doc",
                "xlsx": "sheet", "pptx": "slides", "csv": "sheet"}.get(ext, "text")
        return {"view": "viewer", "rows": [{"title": result.get("created"), "url": url, "kind": kind,
                                            "download": True, "format": result.get("format")}],
                "summary": "Pronto da scaricare."}
    if name in ("sites_status", "today_agenda", "projects", "night_report"):
        rows = result.get("sites") if name == "sites_status" else (
            result.get("tasks") if name == "today_agenda" else result.get("projects"))
        return {"view": name, "rows": [{k: v for k, v in row.items()
                                        if k in ("name", "title", "status", "project", "last_checked", "due")}
                                       for row in (rows or [])[:6] if isinstance(row, dict)],
                "summary": str(result.get("briefing") or result.get("description") or "")[:1200]}
    if name == "ledger_report":
        t = result.get("totals") or {}
        rows = [{"title": e_line(e), "status": e.get("kind"), "day": e.get("day"), "amount": e.get("amount"),
                 "kind": e.get("kind"), "payment": e.get("payment")} for e in (result.get("entries") or [])[:40]]
        return {"view": "ledger", "rows": rows, "totals": t,
                "summary": f"Incassato {t.get('incassato', 0):g} € · uscite {t.get('uscite', 0):g} € · "
                           f"saldo {t.get('saldo_del_periodo', 0):g} €"
                           + (f" · sospesi {t['da_incassare_sospeso']:g} €" if t.get("da_incassare_sospeso") else "")}
    if name == "ledger_add" and result.get("rows"):
        return {"view": "ledger_add", "rows": [{"title": r["row"], "day": r.get("day")} for r in result["rows"]],
                "summary": "Segnato nel registro."}
    if name == "ledger_customer" and result.get("customer"):
        rows = [{"title": h["row"], "status": "sospeso" if h.get("payment") == "sospeso" else h.get("kind"),
                 "day": h.get("day"), "amount": h.get("amount")} for h in reversed(result.get("history") or [])]
        return {"view": "ledger_customer", "rows": rows[:25], "title": result["customer"],
                "summary": f"Speso {result.get('spent', 0):g} € · da pagare {result.get('due', 0):g} € · "
                           f"{result.get('pieces', 0)} pezzi"}
    if name == "ledger_pay" and result.get("saved"):
        rows = [{"title": result["incasso"], "status": "vendita", "day": result.get("day")}]
        rows += [{"title": c["row"], "status": "sospeso" if "SOSPESO" in c["row"] else "done", "day": c.get("day")}
                 for c in result.get("covered") or []]
        return {"view": "ledger_pay", "rows": rows, "summary": f"Da pagare ancora: {result.get('still_due', 0):g} €"}
    if name == "ledger_open":
        rows = [{"title": r["row"], "status": "sospeso", "day": r.get("day")} for r in result.get("pagamenti_sospesi", [])]
        rows += [{"title": r["row"], "status": "da_fare", "day": r.get("day")} for r in result.get("da_fare", [])]
        return {"view": "ledger_open", "rows": rows[:20],
                "summary": f"Da incassare: {result.get('totale_sospeso', 0):g} €"}
    return None


def e_line(e: dict) -> str:
    what = (f"{e['qty']} × " if (e.get("qty") or 1) > 1 else "") + (e.get("item") or "")
    if e.get("size"):
        what += f" {e['size']}"
    if e.get("counterpart"):
        what += f" — {e['counterpart']}"
    return what
