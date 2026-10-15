"""The register on paper: one clear PDF for a day or a period, made by the program (not written by a model).

What Gaetano asked on 15/10: each day closed and the next one below with its title («Martedì 13 ottobre»), entrate
on the left and uscite on the right, a better font, bold titles; then the totals. Every number comes from the
same `Ledger.report` the voice reads, so paper and voice always agree.
"""
from __future__ import annotations

import io
from datetime import date, timedelta

from nodo.ledger import Ledger, due, euro, sentence

_DAYS = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"]


def day_title(d: date) -> str:
    return f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]} {d.year}"


def _money(v: float | None) -> str:
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def lines_for_day(led: Ledger, d: date) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict]:
    """(entrate, uscite, totals) for one day: each line = (what, amount)."""
    rep = led.report(d, d)
    rows = [r for r in led.entries(d, d)]
    ins: list[tuple[str, str]] = []
    outs: list[tuple[str, str]] = []
    for r in rows:
        what = sentence(r).rstrip(".")
        if r.kind in ("vendita", "credito"):
            if r.payment == "sospeso" or r.paid_amount:
                left = due(r)
                tag = f"sospeso, restano {_money(left)}" if r.paid_amount else f"sospeso {_money(r.amount)}"
                ins.append((f"{what}", tag))
            elif r.payment == "pagato":
                ins.append((what, _money(r.amount)))
            else:
                ins.append((what, "omaggio" if not r.amount else _money(r.amount)))
        elif r.kind == "incasso":
            ins.append((what, _money(r.amount)))
        elif r.kind == "spesa":
            outs.append((what, _money(r.amount)))
        else:                                       # replacements, notes: on the left, without money
            ins.append((what, "—"))
    for r in led.settled_in(d, d):
        ins.append((f"saldato oggi: {sentence(r).rstrip('.')}", _money(r.amount)))
    return ins, outs, rep["totals"]


def build(led: Ledger, start: date, end: date, business: str) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from nodo.services.documents import _pdf_fonts, _safe
    reg, bold, _ = _pdf_fonts()
    builtin = reg == "Helvetica"

    def t(x: str) -> str:
        return escape(_safe(x, builtin))
    ink, gold, green, red, line, band = (colors.HexColor("#1d2230"), colors.HexColor("#a8812f"),
                                         colors.HexColor("#1f7a4d"), colors.HexColor("#b03a2e"),
                                         colors.HexColor("#d9dce3"), colors.HexColor("#f4f1ea"))
    base = ParagraphStyle("b", fontName=reg, fontSize=9.6, leading=12.6, textColor=ink)
    small = ParagraphStyle("s", parent=base, fontSize=8.6, leading=11, textColor=colors.HexColor("#5b6070"))
    head = ParagraphStyle("h", parent=base, fontName=bold, fontSize=10, leading=13, textColor=colors.white)
    title = ParagraphStyle("t", parent=base, fontName=bold, fontSize=20, leading=25, textColor=ink)
    day_h = ParagraphStyle("d", parent=base, fontName=bold, fontSize=13.5, leading=17, textColor=gold,
                           spaceBefore=10, spaceAfter=5)
    amount = ParagraphStyle("a", parent=base, fontName=bold, alignment=2)

    story = [Paragraph(t(f"Registro di {business}"), title),
             Paragraph(t(day_title(start) if start == end else f"dal {day_title(start)} al {day_title(end)}"), small),
             Spacer(1, 0.4 * cm)]
    width = A4[0] - 3.2 * cm
    col = width / 2 - 0.2 * cm
    d = start
    days = 0
    while d <= end:
        ins, outs, tot = lines_for_day(led, d)
        if ins or outs:
            days += 1
            n = max(len(ins), len(outs), 1)
            data = [[Paragraph("ENTRATE", head), "", Paragraph("USCITE", head), ""]]
            for i in range(n):
                a = ins[i] if i < len(ins) else ("", "")
                b = outs[i] if i < len(outs) else ("", "")
                data.append([Paragraph(t(a[0]), base), Paragraph(t(a[1]), amount),
                             Paragraph(t(b[0]), base), Paragraph(t(b[1]), amount)])
            data.append([Paragraph(t("Incassato"), base), Paragraph(t(_money(tot["incassato"])), amount),
                         Paragraph(t("Uscite"), base), Paragraph(t(_money(tot["uscite"])), amount)])
            tb = Table(data, colWidths=[col * 0.68, col * 0.32, col * 0.68, col * 0.32], repeatRows=1)
            tb.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (1, 0), green), ("BACKGROUND", (2, 0), (3, 0), red),
                ("SPAN", (0, 0), (1, 0)), ("SPAN", (2, 0), (3, 0)),
                ("LINEAFTER", (1, 0), (1, -1), 1.2, colors.white), ("LINEBEFORE", (2, 1), (2, -1), 0.8, line),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, line),
                ("BACKGROUND", (0, -1), (-1, -1), band), ("LINEABOVE", (0, -1), (-1, -1), 0.9, ink),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
            saldo = tot["saldo_del_periodo"]
            foot = f"Saldo del giorno: {_money(saldo)}"
            if tot.get("da_incassare_sospeso"):
                foot += f"   ·   sospesi del giorno: {_money(tot['da_incassare_sospeso'])}"
            story.append(KeepTogether([Paragraph(t(day_title(d)), day_h), tb, Spacer(1, 0.12 * cm),
                                       Paragraph(t(foot), small), Spacer(1, 0.25 * cm)]))
        d += timedelta(days=1)
    if not days:
        story.append(Paragraph(t("Nessun movimento segnato in questo periodo."), base))
    rep = led.report(start, end)
    tot = rep["totals"]
    summary = [[Paragraph(t("RIEPILOGO" if start == end else "RIEPILOGO DEL PERIODO"), head), ""],
               [Paragraph(t("Incassato"), base), Paragraph(t(_money(tot["incassato"])), amount)],
               [Paragraph(t("Uscite"), base), Paragraph(t(_money(tot["uscite"])), amount)],
               [Paragraph(t("Saldo"), base), Paragraph(t(_money(tot["saldo_del_periodo"])), amount)],
               [Paragraph(t("Ancora da incassare (sospesi)"), base),
                Paragraph(t(_money(tot["da_incassare_sospeso"])), amount)]]
    sb = Table(summary, colWidths=[width * 0.7, width * 0.3])
    sb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), ink), ("SPAN", (0, 0), (1, 0)),
                            ("BOX", (0, 0), (-1, -1), 1, ink), ("LINEBELOW", (0, 1), (-1, -2), 0.25, line),
                            ("BACKGROUND", (0, 3), (-1, 3), band), ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [Spacer(1, 0.4 * cm), KeepTogether([sb])]
    open_rows = led.open_payments()
    if open_rows:
        story += [Spacer(1, 0.3 * cm), Paragraph(t("Chi deve ancora pagare (a oggi)"), day_h)]
        story += [Paragraph(t(f"• {sentence(r).rstrip('.')} — dal {r.day[8:10]}/{r.day[5:7]}: {_money(due(r))}"), base)
                  for r in open_rows[:40]]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm,
                      bottomMargin=1.5 * cm, title=f"Registro {business}", author="NODO").build(story)
    return buf.getvalue()


def file_name(start: date, end: date) -> str:
    return f"registro-{start:%d-%m}" + ("" if start == end else f"-al-{end:%d-%m}") + ".pdf"


__all__ = ["build", "day_title", "file_name", "lines_for_day", "euro"]
