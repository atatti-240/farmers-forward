"""Fertilizer Plan blocks for the PDF reports (plans from backend/fert_calc.py).

Kept out of pdf_report.py so that file only gains its call sites. The stored
plan carries its own wording, so the PDF says exactly what the results page
says. Results saved before the calculator existed have no plan, and get no
block.
"""

from __future__ import annotations

from html import escape
from typing import Dict, List

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

# The results page's amber warning colours (brand.css .ff-badge--amber).
_AMBER_BG = colors.HexColor("#faedcc")
_AMBER_TEXT = colors.HexColor("#79531a")
_AMBER_EDGE = colors.HexColor("#e0a13c")


def _warning(text: str, styles: Dict[str, ParagraphStyle]) -> Table:
    style = ParagraphStyle("ffFertWarn", parent=styles["body"], textColor=_AMBER_TEXT,
                           spaceAfter=0)
    box = Table([[Paragraph("<b>Warning:</b> " + escape(text), style)]],
                colWidths=[7.0 * inch])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _AMBER_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 3, _AMBER_EDGE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return box


def section_flowables(section: Dict, styles: Dict[str, ParagraphStyle]) -> List:
    """One section's Fertilizer Plan, or [] when its result has none."""
    plan = section.get("fertilizer_plan")
    if not plan:
        return []
    heading = ParagraphStyle("ffFertH2", parent=styles["h2"], keepWithNext=1)
    detail = ParagraphStyle("ffFertDetail", parent=styles["muted"], leftIndent=12,
                            spaceAfter=3)
    flow: List = [Paragraph("Fertilizer Plan", heading)]
    if not plan.get("available"):
        flow.append(Paragraph(escape(plan.get("message") or ""), styles["body"]))
        return flow
    flow.append(Paragraph(escape(plan.get("area_text") or ""), styles["muted"]))
    flow.append(Spacer(1, 4))
    for item in plan.get("items") or []:
        if item["kind"] == "product":
            flow.append(Paragraph("<b>%s:</b> %s" % (escape(item["label"]),
                                                     escape(item["amounts"])), styles["body"]))
            flow.append(Paragraph(escape(item["detail"]), detail))
        else:
            flow.append(Paragraph(escape(item["text"]), styles["body"]))
    for text in plan.get("warnings") or []:
        flow += [Spacer(1, 4), _warning(text, styles)]
    if plan.get("warnings"):
        flow.append(Spacer(1, 4))
    for note in plan.get("notes") or []:
        flow.append(Paragraph(escape(note), styles["muted"]))
    if plan.get("source"):
        flow.append(Paragraph(escape(plan["source"]), styles["muted"]))
    return flow


def field_total_flowables(analysis: Dict, styles: Dict[str, ParagraphStyle]) -> List:
    """The Whole Field Total page, or [] for results saved without one."""
    total = analysis.get("fertilizer_total")
    if not total or not (total.get("products") or total.get("excluded")
                         or total.get("n_samples")):
        return []
    # pdf_report imports this module, so its helpers are looked up at call time.
    from backend import pdf_report

    flow: List = [PageBreak()] + pdf_report._header_block(styles)
    flow.append(Paragraph("Whole Field Total", styles["h1"]))
    flow.append(Paragraph(escape(total.get("summary") or ""), styles["body"]))
    flow.append(Spacer(1, 6))

    products = total.get("products") or []
    if products:
        with_cost = any(p.get("cost_total") is not None for p in products)
        rows = [["Product", "Total (lbs)", "%g-lb bags" % total.get("bag_lb", 50)]
                + (["Cost"] if with_cost else [])]
        for p in products:
            rows.append([p["label"], "{:,.0f}".format(p["total_lb"]),
                         p.get("bags_text") or str(p["bags"])]
                        + (["${:,.2f}".format(p["cost_total"]) if p.get("cost_total") is not None
                            else "-"] if with_cost else []))
        widths = [2.7, 1.2, 1.9] + ([1.2] if with_cost else [])
        tbl = Table(rows, colWidths=[w * inch for w in widths])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), pdf_report.FF_BLUE_SOFT),
            ("TEXTCOLOR", (0, 0), (-1, 0), pdf_report.FF_BLUE_DARK),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 1), (-1, -1), pdf_report.FF_TEXT),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, pdf_report.FF_BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, pdf_report.FF_BLUE_SOFT]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        flow.append(tbl)
    elif total.get("none_needed"):
        flow.append(Paragraph("No fertilizer needed across the field.", styles["body"]))

    if total.get("excluded"):
        flow.append(Spacer(1, 8))
        flow.append(Paragraph("Not included", styles["h2"]))
        for text in total["excluded"]:
            flow.append(Paragraph("&#8226; " + escape(text), styles["muted"]))
    flow.append(Spacer(1, 8))
    flow.append(Paragraph(
        "Per-acre rates, pH and salt warnings, and the Texas A&amp;M table behind "
        "each number are in each section's Fertilizer Plan.", styles["muted"]))
    return flow
