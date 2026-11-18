"""Excel and PDF output: reports (FR-29) and invoices (FR-21, MSG-58)."""
from __future__ import annotations

import io
import unicodedata
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..errors import ServiceError

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]
_fonts: tuple[str, str, bool] | None = None


def _setup_fonts() -> tuple[str, str, bool]:
    """Use a Unicode TrueType font when one exists so Vietnamese names print correctly;
    otherwise fall back to Helvetica and strip the accents."""
    global _fonts
    if _fonts:
        return _fonts
    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont("HotelSans", regular))
            pdfmetrics.registerFont(TTFont("HotelSans-Bold", bold))
            _fonts = ("HotelSans", "HotelSans-Bold", True)
            return _fonts
    _fonts = ("Helvetica", "Helvetica-Bold", False)
    return _fonts


def _t(text, unicode_ok: bool) -> str:
    text = "" if text is None else str(text)
    if unicode_ok:
        return text
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def vnd(n) -> str:
    return f"{int(n):,} VND"


# --------------------------------------------------------------- report tables
def report_table(report: dict) -> dict:
    """One neutral table (title, columns, rows, total) used by both exporters."""
    s, e = report["start"].strftime("%d/%m/%Y"), report["end"].strftime("%d/%m/%Y")
    if report["kind"] == "revenue":
        cols = ["Period", "Payments (VND)", "Refunds (VND)", "Revenue (VND)"]
        rows = [[r["label"], r["payments"], r["refunds"], r["net"]] for r in report["rows"]]
        t = report["total"]
        total = ["Total", t["payments"], t["refunds"], t["net"]]
        title = "Revenue report"
    else:
        cols = ["Period", "Occupied room-nights", "Rooms in service", "Days", "Occupancy (%)"]
        rows = [[r["label"], r["nights"], r["rooms"], r["days"], r["rate"]] for r in report["rows"]]
        t = report["total"]
        total = ["Total", t["nights"], t["rooms"], t["days"], t["rate"]]
        title = "Occupancy report"
    return {"title": title, "subtitle": f"{s} to {e}, grouped by {report['group']}",
            "columns": cols, "rows": rows, "total": total}


def to_xlsx(report: dict) -> bytes:
    tb = report_table(report)
    wb = Workbook()
    ws = wb.active
    ws.title = tb["title"][:30]
    ws.append([f"Grand Horizon Hotel - {tb['title']}"])
    ws.append([tb["subtitle"]])
    ws.append([])
    ws.append(tb["columns"])
    head_row = ws.max_row
    for c in ws[head_row]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="14372F")
        c.alignment = Alignment(horizontal="center")
    for r in tb["rows"]:
        ws.append(r)
    ws.append(tb["total"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    ws["A1"].font = Font(bold=True, size=14)
    for i, col in enumerate(tb["columns"], start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(16, len(col) + 4)
        for row in ws.iter_rows(min_row=head_row + 1, min_col=i, max_col=i):
            for c in row:
                if isinstance(c.value, int) and "VND" in col:
                    c.number_format = "#,##0"
    if report["kind"] == "revenue" and report.get("by_type"):
        ws.append([])
        ws.append(["Revenue by room type"])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        for r in report["by_type"]:
            ws.append([r["name"], r["net"], f"{r['share']}%"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf_doc(buf, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                             topMargin=16 * mm, bottomMargin=16 * mm, title=title)


def to_pdf(report: dict) -> bytes:
    try:
        regular, bold, uni = _setup_fonts()
        tb = report_table(report)
        buf = io.BytesIO()
        styles = getSampleStyleSheet()
        h = ParagraphStyle("h", parent=styles["Title"], fontName=bold, fontSize=18, textColor=colors.HexColor("#14372F"))
        p = ParagraphStyle("p", parent=styles["Normal"], fontName=regular, fontSize=10)
        flow = [Paragraph(_t(f"Grand Horizon Hotel - {tb['title']}", uni), h), Paragraph(_t(tb["subtitle"], uni), p),
                Spacer(1, 8)]
        fmt = lambda col, v: f"{int(v):,}" if isinstance(v, int) and "VND" in col else str(v)  # noqa: E731
        data = [[_t(c, uni) for c in tb["columns"]]]
        for r in tb["rows"] + [tb["total"]]:
            data.append([_t(fmt(c, v), uni) for c, v in zip(tb["columns"], r)])
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), regular), ("FONTNAME", (0, 0), (-1, 0), bold),
            ("FONTNAME", (0, -1), (-1, -1), bold), ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14372F")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DFDBD0")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F3EAD8")),
        ]))
        flow.append(table)
        if report["kind"] == "revenue" and report.get("by_type"):
            flow += [Spacer(1, 14), Paragraph(_t("Revenue by room type", uni), p)]
            rows = [["Room type", "Revenue (VND)", "Share"]] + [
                [_t(r["name"], uni), f"{r['net']:,}", f"{r['share']}%"] for r in report["by_type"]]
            t2 = Table(rows)
            t2.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), regular), ("FONTNAME", (0, 0), (-1, 0), bold),
                                    ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DFDBD0"))]))
            flow.append(t2)
        _pdf_doc(buf, tb["title"]).build(flow)
        return buf.getvalue()
    except Exception as exc:  # MSG-58
        raise ServiceError("PDF_FAILED") from exc


# --------------------------------------------------------------------- invoice
def invoice_pdf(invoice, bill_lines) -> bytes:
    """A printable invoice (FR-21). `invoice` is an invoice row joined with its booking."""
    try:
        regular, bold, uni = _setup_fonts()
        buf = io.BytesIO()
        styles = getSampleStyleSheet()
        h = ParagraphStyle("h", parent=styles["Title"], fontName=bold, fontSize=20, textColor=colors.HexColor("#14372F"))
        p = ParagraphStyle("p", parent=styles["Normal"], fontName=regular, fontSize=10, leading=14)
        ci = invoice["check_in_date"].split("-")[::-1]
        co = invoice["check_out_date"].split("-")[::-1]
        flow = [
            Paragraph(_t("Grand Horizon Hotel", uni), h),
            Paragraph(_t(f"Invoice {invoice['invoice_no']}", uni), p),
            Paragraph(_t(f"Issued {invoice['issued_at']}", uni), p),
            Spacer(1, 6),
            Paragraph(_t(f"Guest: {invoice['guest_name']}   Booking: {invoice['booking_code']}", uni), p),
            Paragraph(_t(f"{invoice['type_name']}, room {invoice['room_number'] or '-'}, "
                         f"{'/'.join(ci)} to {'/'.join(co)}", uni), p),
            Spacer(1, 10),
        ]
        rows = [["Description", "Amount (VND)"], ["Room charge", f"{int(invoice['room_charge']):,}"]]
        for u in bill_lines:
            rows.append([f"{u['name']} x {u['quantity']}", f"{int(u['quantity']) * int(u['unit_price']):,}"])
        if int(invoice["late_fee"]):
            rows.append(["Late check-out fee", f"{int(invoice['late_fee']):,}"])
        rows.append(["VAT", f"{int(invoice['vat_amount']):,}"])
        rows.append(["Total", f"{int(invoice['total_amount']):,}"])
        rows = [[_t(a, uni), b] for a, b in rows]
        table = Table(rows, colWidths=[110 * mm, 50 * mm])
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), regular), ("FONTNAME", (0, 0), (-1, 0), bold),
            ("FONTNAME", (0, -1), (-1, -1), bold), ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14372F")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#DFDBD0")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F3EAD8")),
        ]))
        flow += [table, Spacer(1, 10), Paragraph(_t("Balance due: 0 VND. This invoice is final and cannot be edited.", uni), p)]
        _pdf_doc(buf, f"Invoice {invoice['invoice_no']}").build(flow)
        return buf.getvalue()
    except Exception as exc:  # MSG-58: the guest can print it again
        raise ServiceError("PDF_FAILED") from exc
