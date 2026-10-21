"""PDF renderer (fpdf2 + HarfBuzz shaping so Thai vowels and tone marks sit correctly).

Fonts: Sarabun (OFL, bundled in report/fonts) for Thai and Latin; DejaVu Sans from matplotlib
as fallback for symbols such as ✓ ✗ ⚠ → that Sarabun lacks. Both are embedded in the PDF.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from gpr_analyzer.report.compose import Bullets, Figure, Heading, PageBreak, Para, Report, Table

FONT_DIR = Path(__file__).parent / "fonts"

# light fills per action level, used in zone tables
ACTION_FILL = {"E": (248, 215, 218), "1": (253, 226, 204), "2": (255, 243, 205), "3": (214, 230, 250),
               "none": (255, 255, 255)}
HEAD_FILL = (232, 236, 241)
TEXT = (28, 32, 38)
MUTED = (95, 102, 112)


def _fallback_font() -> str | None:
    import matplotlib

    p = os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans.ttf")
    return p if os.path.exists(p) else None


def _make_pdf(report: Report):
    from fpdf import FPDF

    class _PDF(FPDF):
        def header(self) -> None:
            if report.draft:
                self.set_font("Sarabun", "B", 60)
                self.set_text_color(238, 238, 238)
                with self.rotation(35, self.w / 2, self.h / 2):
                    self.text(self.w / 2 - 35, self.h / 2, report.draft_mark)
                self.set_text_color(*TEXT)

        def footer(self) -> None:
            self.set_y(-12)
            self.set_font("Sarabun", "", 7.5)
            self.set_text_color(*MUTED)
            self.cell(0, 5, report.footer, align="L")
            self.set_x(self.l_margin)
            self.cell(0, 5, f"{report.page_label} {self.page_no()}/{{nb}}", align="R")
            self.set_text_color(*TEXT)

    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_font("Sarabun", "", str(FONT_DIR / "Sarabun-Regular.ttf"))
    pdf.add_font("Sarabun", "B", str(FONT_DIR / "Sarabun-Bold.ttf"))
    fb = _fallback_font()
    if fb:
        pdf.add_font("DejaVu", "", fb)
        pdf.add_font("DejaVu", "B", fb)
        pdf.set_fallback_fonts(["DejaVu"], exact_match=False)
    pdf.set_text_shaping(True)
    pdf.set_title(report.title)
    pdf.set_creator("gpr-analyzer")
    pdf.set_lang("th" if report.lang in ("th", "bi") else "en")
    return pdf


def _table(pdf, b: Table, base: float) -> None:
    from fpdf.fonts import FontFace

    widths = b.widths or [1] * len(b.rows[0] if b.rows else b.header)
    size = base - (1.5 if len(widths) > 6 else 0.5)
    pdf.set_font("Sarabun", "", size)
    head = FontFace(emphasis="BOLD", fill_color=HEAD_FILL)
    with pdf.table(col_widths=widths, line_height=size * 0.5, padding=1.0,
                   first_row_as_headings=bool(b.header), headings_style=head,
                   borders_layout="SINGLE_TOP_LINE" if b.kind == "kv" else "ALL",
                   text_align="LEFT") as t:
        if b.header:
            row = t.row()
            for h in b.header:
                row.cell(h)
        for i, r in enumerate(b.rows):
            tag = b.row_tags[i] if b.row_tags else None
            row = t.row()
            for j, c in enumerate(r):
                style = None
                if b.kind == "kv" and j == 0:
                    style = FontFace(emphasis="BOLD", color=MUTED)
                elif tag is not None and j == 0:
                    style = FontFace(emphasis="BOLD", fill_color=ACTION_FILL.get(tag))
                row.cell(c, style=style)
    pdf.ln(2)


def render_pdf(report: Report, path: str | os.PathLike | None = None, base_size: float = 10) -> bytes:
    """Draw the report; write to path if given and always return the PDF bytes."""
    pdf = _make_pdf(report)
    pdf.add_page()
    pdf.set_text_color(*TEXT)
    pdf.set_font("Sarabun", "B", 17)
    pdf.multi_cell(0, 8, report.title, new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_font("Sarabun", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 5, report.subtitle, new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_text_color(*TEXT)
    if report.draft:
        pdf.ln(2)
        pdf.set_fill_color(255, 243, 205)
        pdf.set_font("Sarabun", "B", 9)
        pdf.multi_cell(0, 5, "\n".join(report.draft_text), fill=True, padding=2, new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.set_fill_color(255, 255, 255)
    pdf.ln(3)

    usable = pdf.w - pdf.l_margin - pdf.r_margin
    for b in report.blocks:
        if isinstance(b, Heading):
            size = {1: 14, 2: 12, 3: 10.5}.get(b.level, 10)
            if pdf.get_y() > pdf.h - 45:
                pdf.add_page()
            pdf.ln(2 if b.level > 1 else 3)
            pdf.set_font("Sarabun", "B", size)
            pdf.multi_cell(0, size * 0.5, b.text, new_x="LMARGIN", new_y="NEXT", align="L")
            pdf.ln(1)
        elif isinstance(b, Para):
            size = {"secondary": base_size - 1, "small": base_size - 1.5, "caption": base_size - 1.5}.get(
                b.style, base_size)
            pdf.set_font("Sarabun", "", size)
            pdf.set_text_color(*(MUTED if b.style in ("secondary", "small", "caption") else TEXT))
            pdf.multi_cell(0, size * 0.48, b.text, new_x="LMARGIN", new_y="NEXT", align="L")
            pdf.set_text_color(*TEXT)
            pdf.ln(1.2)
        elif isinstance(b, Bullets):
            pdf.set_font("Sarabun", "", base_size - 0.5)
            for item in b.items:
                pdf.set_x(pdf.l_margin + 2)
                pdf.multi_cell(usable - 2, (base_size - 0.5) * 0.48, "• " + item, new_x="LMARGIN", new_y="NEXT", align="L")
                pdf.ln(0.6)
            pdf.ln(1)
        elif isinstance(b, Table):
            _table(pdf, b, base_size)
        elif isinstance(b, Figure):
            w = usable * b.width
            # 7 x 3.2 in figure aspect; keep the figure and its caption together
            h = w * 3.2 / 7.0
            if pdf.get_y() + h + 12 > pdf.h - pdf.b_margin:
                pdf.add_page()
            pdf.image(io.BytesIO(b.png), x=pdf.l_margin + (usable - w) / 2, w=w)
            pdf.set_font("Sarabun", "", base_size - 1.5)
            pdf.set_text_color(*MUTED)
            pdf.multi_cell(0, (base_size - 1.5) * 0.48, "\n".join(b.caption), new_x="LMARGIN", new_y="NEXT", align="L")
            pdf.set_text_color(*TEXT)
            pdf.ln(2)
        elif isinstance(b, PageBreak):
            pdf.add_page()
    out = bytes(pdf.output())
    if path is not None:
        Path(path).write_bytes(out)
    return out
