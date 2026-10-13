"""Documents NODO can write: PDF, Word, Excel, PowerPoint.

Input is plain Markdown (headings, paragraphs, lists, tables, **bold**, *italic*, `code`, ---): the model already
writes it naturally, and the same text gives a PDF, a .docx, a slide deck or - tables only - a spreadsheet.
Pure Python (reportlab, python-docx, openpyxl, python-pptx): nothing to install besides NODO's own dependencies,
no browser and no Office needed on the PC.
"""
from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

FORMATS = ("pdf", "docx", "xlsx", "pptx")

_INLINE = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|`[^`]+`)")


@dataclass
class Block:
    kind: str                                   # h1 h2 h3 p ul ol table code hr
    text: str = ""
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ parsing
def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_markdown(text: str) -> list[Block]:
    blocks: list[Block] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    para: list[str] = []

    def flush():
        if para:
            blocks.append(Block("p", " ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            code, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(code)))
        elif not s:
            flush()
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            flush()
            blocks.append(Block("hr"))
        elif m := re.match(r"(#{1,6})\s+(.*)", s):
            flush()
            blocks.append(Block(f"h{min(len(m.group(1)), 3)}", m.group(2).strip()))
        elif s.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?",
                                                                    lines[i + 1].strip()):
            flush()
            rows = [_cells(s)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            i -= 1
            width = max(len(r) for r in rows)
            blocks.append(Block("table", rows=[r + [""] * (width - len(r)) for r in rows]))
        elif re.match(r"[-*•]\s+", s):
            flush()
            items = []
            while i < len(lines) and re.match(r"\s*[-*•]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*•]\s+", "", lines[i]).strip())
                i += 1
            i -= 1
            blocks.append(Block("ul", items=items))
        elif re.match(r"\d+[.)]\s+", s):
            flush()
            items = []
            while i < len(lines) and re.match(r"\s*\d+[.)]\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+[.)]\s+", "", lines[i]).strip())
                i += 1
            i -= 1
            blocks.append(Block("ol", items=items))
        else:
            para.append(s)
        i += 1
    flush()
    return blocks


def _runs(text: str) -> list[tuple[str, str]]:
    """[(text, style)] with style in '', 'b', 'i', 'c' (code)."""
    out = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith(("**", "__")) and part.endswith(("**", "__")) and len(part) > 4:
            out.append((part[2:-2], "b"))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append((part[1:-1], "i"))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append((part[1:-1], "c"))
        else:
            out.append((part, ""))
    return out


def plain(text: str) -> str:
    return "".join(t for t, _ in _runs(text))


# ------------------------------------------------------------------------------------------------ PDF
_FONT_FILES = [
    # (regular, bold, italic) - first set that exists wins; these carry every Italian accent and typographic quote
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf", "C:/Windows/Fonts/calibrii.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
]


def _pdf_fonts() -> tuple[str, str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for reg, bold, ital in _FONT_FILES:
        if Path(reg).exists() and Path(bold).exists() and Path(ital).exists():
            try:
                for name, path in (("NodoSans", reg), ("NodoSans-Bold", bold), ("NodoSans-Italic", ital)):
                    if name not in pdfmetrics.getRegisteredFontNames():
                        pdfmetrics.registerFont(TTFont(name, path))
                return "NodoSans", "NodoSans-Bold", "NodoSans-Italic"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"      # built in: Latin-1 only (see _safe)


def _safe(text: str, builtin: bool) -> str:
    if not builtin:
        return text
    return text.encode("cp1252", "replace").decode("cp1252")        # accents stay, exotic symbols become '?'


def make_pdf(markdown: str, title: str | None = None) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        Paragraph,
        Preformatted,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    reg, bold, ital = _pdf_fonts()
    builtin = reg == "Helvetica"
    ink, accent, line = colors.HexColor("#1f2430"), colors.HexColor("#0b5fff"), colors.HexColor("#d5d9e2")
    base = ParagraphStyle("base", fontName=reg, fontSize=10.5, leading=15, textColor=ink, alignment=TA_LEFT,
                          spaceAfter=7)
    h = {1: ParagraphStyle("h1", parent=base, fontName=bold, fontSize=21, leading=26, spaceBefore=4, spaceAfter=10,
                           textColor=accent),
         2: ParagraphStyle("h2", parent=base, fontName=bold, fontSize=15, leading=20, spaceBefore=12, spaceAfter=6),
         3: ParagraphStyle("h3", parent=base, fontName=bold, fontSize=12, leading=16, spaceBefore=8, spaceAfter=4)}
    cell = ParagraphStyle("cell", parent=base, fontSize=9.5, leading=12.5, spaceAfter=0)
    cell_head = ParagraphStyle("cellh", parent=cell, fontName=bold, textColor=colors.white)

    def inline(text: str) -> str:
        out = []
        for t, st in _runs(text):
            t = escape(_safe(t, builtin))
            out.append({"b": f"<b>{t}</b>", "i": f"<i>{t}</i>", "c": f"<font face='Courier'>{t}</font>"}.get(st, t))
        return "".join(out)

    story = []
    blocks = parse_markdown(markdown)
    if title and not (blocks and blocks[0].kind == "h1"):
        story.append(Paragraph(inline(title), h[1]))
    for b in blocks:
        if b.kind in ("h1", "h2", "h3"):
            story.append(Paragraph(inline(b.text), h[int(b.kind[1])]))
        elif b.kind == "p":
            story.append(Paragraph(inline(b.text), base))
        elif b.kind in ("ul", "ol"):
            items = [ListItem(Paragraph(inline(t), base), leftIndent=14) for t in b.items]
            story.append(ListFlowable(items, bulletType="bullet" if b.kind == "ul" else "1", leftIndent=14,
                                      bulletFontName=reg, bulletFontSize=9 if b.kind == "ul" else 10.5,
                                      start="•" if b.kind == "ul" else None))
            story.append(Spacer(1, 4))
        elif b.kind == "table":
            data = [[Paragraph(inline(c), cell_head if r == 0 else cell) for c in row] for r, row in enumerate(b.rows)]
            t = Table(data, repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), accent), ("GRID", (0, 0), (-1, -1), 0.4, line),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                                   ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")])]))
            story += [t, Spacer(1, 8)]
        elif b.kind == "code":
            story.append(Preformatted(_safe(b.text, builtin), ParagraphStyle(
                "code", parent=base, fontName="Courier", fontSize=8.5, leading=11, backColor=colors.HexColor("#f4f6fa"),
                borderPadding=6, spaceAfter=10)))
        elif b.kind == "hr":
            story.append(HRFlowable(width="100%", color=line, spaceBefore=6, spaceAfter=10))
    if not story:
        story.append(Paragraph("(documento vuoto)", base))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(reg, 8)
        canvas.setFillColor(colors.HexColor("#7a8194"))
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"{doc.page}")
        if title:
            canvas.drawString(2 * cm, 1.2 * cm, _safe(title, builtin)[:90])
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                      title=title or "Documento", author="NODO").build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ Word
def make_docx(markdown: str, title: str | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt
    d = Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    d.core_properties.author = "NODO"
    if title:
        d.core_properties.title = title

    def fill(par, text: str):
        for t, st in _runs(text):
            r = par.add_run(t)
            r.bold = st == "b" or None
            r.italic = st == "i" or None
            if st == "c":
                r.font.name = "Consolas"

    blocks = parse_markdown(markdown)
    if title and not (blocks and blocks[0].kind == "h1"):
        d.add_heading(title, 1)
    for b in blocks:
        if b.kind in ("h1", "h2", "h3"):
            d.add_heading(plain(b.text), int(b.kind[1]))
        elif b.kind == "p":
            fill(d.add_paragraph(), b.text)
        elif b.kind in ("ul", "ol"):
            for t in b.items:
                fill(d.add_paragraph(style="List Bullet" if b.kind == "ul" else "List Number"), t)
        elif b.kind == "table":
            tbl = d.add_table(rows=len(b.rows), cols=len(b.rows[0]))
            tbl.style = "Light Grid Accent 1"
            for r, row in enumerate(b.rows):
                for c, val in enumerate(row):
                    cellp = tbl.cell(r, c).paragraphs[0]
                    fill(cellp, val)
                    if r == 0:
                        for run in cellp.runs:
                            run.bold = True
            d.add_paragraph()
        elif b.kind == "code":
            p = d.add_paragraph()
            run = p.add_run(b.text)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif b.kind == "hr":
            d.add_paragraph("―" * 30)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ Excel
def _number(v: str):
    s = v.strip().replace("€", "").replace("%", "").strip()
    if not s or not re.fullmatch(r"-?[\d.,]+", s):
        return v
    try:
        if "," in s and "." in s:                       # 1.234,56
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:                                  # 12,5
            s = s.replace(",", ".")
        elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):   # 1.234 (thousands)
            s = s.replace(".", "")
        n = float(s)
        return int(n) if n.is_integer() and "." not in s else n
    except ValueError:
        return v


def make_xlsx(markdown: str, title: str | None = None) -> bytes:
    """Every Markdown table becomes a sheet (named after the heading above it). CSV text works too."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    blocks = parse_markdown(markdown)
    tables: list[tuple[str, list[list[str]]]] = []
    last_heading = ""
    for b in blocks:
        if b.kind in ("h1", "h2", "h3"):
            last_heading = plain(b.text)
        elif b.kind == "table":
            tables.append((last_heading or f"Foglio{len(tables) + 1}", b.rows))
    if not tables:                                      # plain CSV / semicolon text
        rows = [re.split(r"\s*[;\t,]\s*", ln.strip()) for ln in markdown.splitlines() if ln.strip()]
        if rows and len(rows[0]) > 1:
            tables.append((title or "Foglio1", rows))
    if not tables:
        raise ValueError("per un foglio Excel serve una tabella (righe e colonne)")
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for name, rows in tables:
        nm = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:28] or "Foglio"
        base, n = nm, 2
        while nm.lower() in used:
            nm, n = f"{base[:25]} {n}", n + 1
        used.add(nm.lower())
        ws = wb.create_sheet(nm)
        for r, row in enumerate(rows, 1):
            for c, val in enumerate(row, 1):
                cell = ws.cell(row=r, column=c, value=plain(val) if r == 1 else _number(plain(val)))
                if r == 1:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="0B5FFF")
                    cell.alignment = Alignment(vertical="center")
        for c in range(1, len(rows[0]) + 1):
            width = max(len(str(plain(r[c - 1]))) for r in rows if c - 1 < len(r))
            ws.column_dimensions[get_column_letter(c)].width = min(max(10, width + 2), 60)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    wb.properties.creator = "NODO"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ PowerPoint
def make_pptx(markdown: str, title: str | None = None) -> bytes:
    """`# Titolo` (or `## `) starts a slide; bullets and short paragraphs under it become its body; a table is
    written as a table slide. The first slide is the cover when `title` is given or the text starts with a `# `."""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slides: list[dict] = []
    for b in parse_markdown(markdown):
        if b.kind in ("h1", "h2"):
            slides.append({"title": plain(b.text), "lines": [], "table": None})
        else:
            if not slides:
                slides.append({"title": title or "", "lines": [], "table": None})
            cur = slides[-1]
            if b.kind in ("ul", "ol"):
                cur["lines"] += [plain(t) for t in b.items]
            elif b.kind == "p":
                cur["lines"].append(plain(b.text))
            elif b.kind == "h3":
                cur["lines"].append(plain(b.text).upper())
            elif b.kind == "table":
                cur["table"] = b.rows
            elif b.kind == "code":
                cur["lines"] += b.text.splitlines()[:8]
    if not slides:
        raise ValueError("nessun contenuto per le slide")
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    blue, ink = RGBColor(0x0B, 0x5F, 0xFF), RGBColor(0x1F, 0x29, 0x37)

    def text(sl, x, y, w, h, value, size, color=ink, bold=False):
        tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tb.text_frame.word_wrap = True
        p = tb.text_frame.paragraphs[0]
        p.text = value
        p.font.size, p.font.bold, p.font.color.rgb = Pt(size), bold, color
        return tb.text_frame

    for n, s in enumerate(slides):
        sl = prs.slides.add_slide(prs.slide_layouts[6])      # blank: we lay everything out for 16:9 ourselves
        bar = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.25), prs.slide_height)
        bar.fill.solid()
        bar.fill.fore_color.rgb = blue
        bar.line.fill.background()
        if n == 0 and not s["lines"] and not s["table"]:     # cover
            text(sl, 1.2, 2.6, 11, 1.6, s["title"] or (title or ""), 48, blue, True)
            text(sl, 1.2, 4.4, 11, 0.6, "NODO", 20, RGBColor(0x6B, 0x72, 0x80))
            continue
        text(sl, 0.8, 0.5, 11.8, 1.0, s["title"], 34, blue, True)
        top = 1.7
        if s["table"]:
            rows, cols = len(s["table"]), len(s["table"][0])
            gt = sl.shapes.add_table(rows, cols, Inches(0.8), Inches(top), Inches(11.7), Inches(0.45) * rows).table
            for r, row in enumerate(s["table"]):
                for c in range(cols):
                    cell = gt.cell(r, c)
                    cell.text = plain(row[c]) if c < len(row) else ""
                    cell.text_frame.paragraphs[0].font.size = Pt(16)
            top += 0.45 * rows + 0.4
        if s["lines"]:
            tf = text(sl, 0.8, top, 11.7, 7.0 - top, "", 22)
            many = len(s["lines"]) > 1
            for k, line in enumerate(s["lines"][:10]):
                p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
                p.text = ("• " if many else "") + line
                p.font.size = Pt(22 if len(s["lines"]) <= 5 else 18)
                p.font.color.rgb = ink
                p.space_after = Pt(8)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def build(fmt: str, markdown: str, title: str | None = None) -> bytes:
    fmt = fmt.lower().lstrip(".")
    fn = {"pdf": make_pdf, "docx": make_docx, "xlsx": make_xlsx, "pptx": make_pptx}.get(fmt)
    if fn is None:
        raise ValueError(f"formato non supportato: {fmt} (usa pdf, docx, xlsx o pptx)")
    return fn(markdown, title)


def extract_text(path: Path) -> str:
    """Plain text of a .docx/.xlsx/.pptx/.pdf (so NODO can read back what it, or Gaetano, wrote)."""
    ext = path.suffix.lower()
    if ext == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            parts += [" | ".join(c.text for c in r.cells) for r in t.rows]
        return "\n".join(parts)
    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            out.append(f"## {ws.title}")
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    out.append(" | ".join("" if v is None else str(v) for v in row))
        return "\n".join(out)
    if ext == ".pptx":
        from pptx import Presentation
        out = []
        for i, sl in enumerate(Presentation(str(path)).slides, 1):
            out.append(f"## Slide {i}")
            out += [sh.text_frame.text for sh in sl.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
        return "\n".join(out)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)
    return ""


def _selftest() -> int:  # python -m nodo.services.documents
    sample = "# Prova\n\nCiao **mondo** è un *test*: perché sì.\n\n- uno\n- due\n\n| A | B |\n|---|---|\n| 1 | 2,5 |\n"
    for f in FORMATS:
        print(f, len(build(f, sample, "Prova")))
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
