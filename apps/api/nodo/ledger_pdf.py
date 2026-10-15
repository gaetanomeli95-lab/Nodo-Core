"""The register on paper: one clear PDF for a day or a period, made by the program (not written by a model).

What Gaetano asked on 15/10: each day closed and the next one below with its title («Giovedì 15 ottobre»), entrate
on the left and uscite on the right, a better font, bold titles; then the totals. The first version printed whole
sentences («2 × materasso … a 140 euro l'uno, totale 280 euro a Rachid …, pagato (…)»): «non si capisce un cavolo».
Now every line is a row of a table: who, what, how many, how much, paid or not. Every number comes from the same
`Ledger.report` the voice reads, so paper and voice always agree.
"""
from __future__ import annotations

import io
import re
from datetime import date, timedelta

from nodo.ledger import Ledger, due

_DAYS = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"]
_CONSIGNMENT = re.compile(r"conto vendita", re.I)


def day_title(d: date) -> str:
    return f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]} {d.year}"


def money(v: float | None) -> str:
    v = round(v or 0, 2)
    text = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return (text[:-3] if text.endswith(",00") else text) + " €"


def _what(r) -> str:
    item = (r.item or r.kind or "").strip()
    item = re.sub(r"^materass[oi] ", "", item, flags=re.I)          # the shop sells mattresses: the model is the news
    return f"{item} {r.size}".strip() if r.size else item


def _note(r) -> str:
    n = (r.note or "").strip()
    return n if len(n) <= 70 else n[:67].rsplit(" ", 1)[0] + "…"


def rows_for_day(led: Ledger, d: date) -> tuple[list[dict], list[dict], dict]:
    """(entrate, uscite, totals) for one day, each row already in words for the table."""
    rep = led.report(d, d)
    ins, outs = [], []
    for r in led.entries(d, d):
        base = {"who": (r.counterpart or "").strip(), "what": _what(r), "qty": r.qty or 1, "note": _note(r)}
        if r.kind in ("vendita", "credito"):
            if not r.amount:
                ins.append({**base, "amount": None, "state": "omaggio"})
            elif r.paid_amount and due(r) > 0.005:
                ins.append({**base, "amount": r.amount, "state": f"resta {money(due(r))}", "open": True})
            elif r.payment == "sospeso" and not r.paid_amount:
                ins.append({**base, "amount": r.amount, "state": "conto vendita" if _CONSIGNMENT.search(r.note or "")
                            else "sospeso", "open": True})
            else:
                ins.append({**base, "amount": r.amount, "state": "pagato"})
        elif r.kind == "incasso":
            ins.append({**base, "what": "acconto / saldo ricevuto", "amount": r.amount, "state": "incassato"})
        elif r.kind == "spesa":
            what = (r.item or "").strip() or (r.counterpart or "").strip() or "spesa"     # «palestra 65»
            outs.append({"what": what, "who": (r.counterpart or "").strip() if r.item else "",
                         "amount": r.amount, "note": _note(r)})
        elif r.kind == "sostituzione":
            ins.append({**base, "amount": None, "state": "cambio"})
        else:
            ins.append({**base, "amount": None, "state": r.kind})
    for r in led.settled_in(d, d):
        ins.append({"who": (r.counterpart or "").strip(), "what": f"saldo di {_what(r)} del {r.day[8:10]}/{r.day[5:7]}",
                    "qty": 1, "note": "", "amount": r.amount, "state": "incassato"})
    return ins, outs, rep["totals"]


def debtors(led: Ledger) -> tuple[list[dict], list[dict]]:
    """Who still has to pay, one line per customer (debts), and the goods left in «conto vendita» apart."""
    groups: dict[tuple[str, bool], dict] = {}
    for r in led.open_payments():
        left = due(r)
        if left <= 0.005:
            continue
        cons = bool(_CONSIGNMENT.search(r.note or ""))
        name = (r.counterpart or "senza nome").strip()
        g = groups.setdefault((name.lower(), cons), {"who": name, "total": 0.0, "items": []})
        g["total"] += left
        g["items"].append(f"{r.day[8:10]}/{r.day[5:7]} {_what(r)}" + (f" ×{r.qty}" if (r.qty or 1) > 1 else "")
                          + f" {money(left)}")
    debts = sorted((g for (_, c), g in groups.items() if not c), key=lambda g: -g["total"])
    cons = sorted((g for (_, c), g in groups.items() if c), key=lambda g: -g["total"])
    return debts, cons


def build(led: Ledger, start: date, end: date, business: str) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from nodo.services.documents import _pdf_fonts, _safe
    reg, bold, ital = _pdf_fonts()
    builtin = reg == "Helvetica"

    def t(x: str) -> str:
        return escape(_safe(str(x), builtin))
    ink, mute, gold = colors.HexColor("#1d2230"), colors.HexColor("#6b7080"), colors.HexColor("#9a7426")
    green, red, line, band = (colors.HexColor("#1f7a4d"), colors.HexColor("#b03a2e"), colors.HexColor("#dfe2e8"),
                              colors.HexColor("#f5f2ea"))
    base = ParagraphStyle("b", fontName=reg, fontSize=9.4, leading=12, textColor=ink)
    who = ParagraphStyle("w", parent=base, fontName=bold)
    note = ParagraphStyle("n", parent=base, fontName=ital, fontSize=8, leading=10, textColor=mute)
    small = ParagraphStyle("s", parent=base, fontSize=8.6, leading=11, textColor=mute)
    right = ParagraphStyle("r", parent=base, fontName=bold, alignment=2)
    head = ParagraphStyle("h", parent=base, fontName=bold, fontSize=9, textColor=colors.white)
    title = ParagraphStyle("t", parent=base, fontName=bold, fontSize=20, leading=25)
    day_h = ParagraphStyle("d", parent=base, fontName=bold, fontSize=13, leading=16, textColor=gold)
    sec_h = ParagraphStyle("sh", parent=day_h, fontSize=12.5, spaceBefore=6, spaceAfter=4)
    state_color = {"pagato": green, "incassato": green, "sospeso": red, "conto vendita": gold, "omaggio": mute}

    def state(s: str) -> Paragraph:
        c = state_color.get(s, red if s.startswith("resta") else mute)
        return Paragraph(f"<font color='{c.hexval()}'><b>{t(s)}</b></font>", ParagraphStyle("st", parent=base,
                                                                                              alignment=1))

    def cell(r: dict) -> list:
        top = f"<b>{t(r['who'])}</b> · {t(r['what'])}" if r.get("who") else t(r["what"])
        out = [Paragraph(top, base)]
        if r.get("note"):
            out.append(Paragraph(t(r["note"]), note))
        return out

    width = A4[0] - 3 * cm
    left_w, gap = width * 0.63, 0.25 * cm
    right_w = width - left_w - gap
    story = [Paragraph(t(f"Registro di {business}"), title),
             Paragraph(t(day_title(start) if start == end else f"dal {day_title(start)} al {day_title(end)}"), small),
             Spacer(1, 0.35 * cm)]
    d, days = start, 0
    while d <= end:
        ins, outs, tot = rows_for_day(led, d)
        if ins or outs:
            days += 1
            # entrate: who · what (note) | pz | importo | stato
            e_data = [[Paragraph("ENTRATE", head), Paragraph("n.", head), Paragraph("importo", head),
                       Paragraph("stato", head)]]
            for r in ins or [{"what": "nessuna entrata", "qty": "", "amount": None, "state": ""}]:
                e_data.append([cell(r), Paragraph(t(r.get("qty") or ""), base),
                               Paragraph(t(money(r["amount"])) if r.get("amount") else "—", right),
                               state(r["state"]) if r.get("state") else ""])
            et = Table(e_data, colWidths=[left_w * 0.54, left_w * 0.08, left_w * 0.18, left_w * 0.20], repeatRows=1)
            et.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), green), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("LINEBELOW", (0, 1), (-1, -1), 0.3, line), ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5), ("ALIGN", (1, 0), (1, -1), "CENTER"),
                                    ("LEFTPADDING", (1, 0), (-1, -1), 3), ("RIGHTPADDING", (1, 0), (-1, -1), 3)]))
            u_data = [[Paragraph("USCITE", head), Paragraph("importo", head)]]
            for r in outs or [{"what": "nessuna uscita", "who": "", "amount": None, "note": ""}]:
                label = t(r["what"]) + (f" · {t(r['who'])}" if r.get("who") and r["who"].lower() != r["what"].lower()
                                        else "")
                c = [Paragraph(label, base)] + ([Paragraph(t(r["note"]), note)] if r.get("note") else [])
                u_data.append([c, Paragraph(t(money(r["amount"])) if r.get("amount") else "—", right)])
            ut = Table(u_data, colWidths=[right_w * 0.68, right_w * 0.32], repeatRows=1)
            ut.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), red), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("LINEBELOW", (0, 1), (-1, -1), 0.3, line), ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
            sides = Table([[et, "", ut]], colWidths=[left_w, gap, right_w])
            sides.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
            sums = Table([[Paragraph(t(f"Incassato {money(tot['incassato'])}"), who),
                           Paragraph(t(f"Uscite {money(tot['uscite'])}"), who),
                           Paragraph(t(f"Saldo {money(tot['saldo_del_periodo'])}"), who),
                           Paragraph(t(f"Restano da incassare {money(tot['da_incassare_sospeso'])}"), who)]],
                         colWidths=[width * 0.22, width * 0.2, width * 0.2, width * 0.38])
            sums.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), band), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                      ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            story.append(KeepTogether([Paragraph(t(day_title(d)), day_h), Spacer(1, 0.12 * cm), sides,
                                       Spacer(1, 0.1 * cm), sums, Spacer(1, 0.45 * cm)]))
        d += timedelta(days=1)
    if not days:
        story.append(Paragraph(t("Nessun movimento segnato in questo periodo."), base))
    if start != end and days:
        tot = led.report(start, end)["totals"]
        box = Table([[Paragraph("TOTALE DEL PERIODO", head), ""],
                     [Paragraph("Incassato", base), Paragraph(t(money(tot["incassato"])), right)],
                     [Paragraph("Uscite", base), Paragraph(t(money(tot["uscite"])), right)],
                     [Paragraph("<b>Saldo</b>", base), Paragraph(t(money(tot["saldo_del_periodo"])), right)],
                     [Paragraph("Vendite del periodo ancora da incassare", base),
                      Paragraph(t(money(tot["da_incassare_sospeso"])), right)]], colWidths=[width * 0.72, width * 0.28])
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), ink), ("SPAN", (0, 0), (1, 0)),
                                 ("BOX", (0, 0), (-1, -1), 0.8, ink), ("LINEBELOW", (0, 1), (-1, -2), 0.3, line),
                                 ("BACKGROUND", (0, 3), (-1, 3), band), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [KeepTogether([box]), Spacer(1, 0.4 * cm)]
    debts, cons = debtors(led)
    for label, groups in (("Chi deve ancora pagare (a oggi)", debts),
                          ("In conto vendita (si paga quando si vende)", cons)):
        if not groups:
            continue
        data = [[Paragraph("CLIENTE", head), Paragraph("COSA (giorno, articolo, quanto resta)", head),
                 Paragraph("TOTALE", head)]]
        for g in groups:
            data.append([Paragraph(t(g["who"]), who), Paragraph("<br/>".join(t(i) for i in g["items"][:8])
                                                                 + ("<br/>…" if len(g["items"]) > 8 else ""), small),
                         Paragraph(t(money(g["total"])), right)])
        data.append([Paragraph("<b>Totale</b>", base), "", Paragraph(t(money(sum(g["total"] for g in groups))), right)])
        tb = Table(data, colWidths=[width * 0.26, width * 0.56, width * 0.18], repeatRows=1)
        tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), gold if groups is cons else red),
                                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 1), (-1, -1), 0.3, line),
                                ("BACKGROUND", (0, -1), (-1, -1), band), ("TOPPADDING", (0, 0), (-1, -1), 4),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story += [Paragraph(t(label), sec_h), tb, Spacer(1, 0.35 * cm)]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.4 * cm,
                      bottomMargin=1.4 * cm, title=f"Registro {business}", author="NODO").build(story)
    return buf.getvalue()


def file_name(start: date, end: date) -> str:
    return f"registro-{start:%d-%m}" + ("" if start == end else f"-al-{end:%d-%m}") + ".pdf"
