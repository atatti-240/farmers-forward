"""Brand-styled PDF report generation for Farmers Forward (ReportLab).

Two public builders:
  * build_section_pdf(section, farm_name, date) -> bytes   (one section)
  * build_combined_pdf(analysis) -> bytes                  (cover + all sections)

Both return raw PDF bytes rendered via SimpleDocTemplate into a BytesIO buffer.
Colors come from the brand tokens in BUILD_CONTRACT §1.
"""

from __future__ import annotations

import io
from html import escape
from typing import Dict, List

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Brand palette (BUILD_CONTRACT §1)
# ---------------------------------------------------------------------------
FF_BLUE = colors.HexColor("#4AAFE0")
FF_BLUE_DARK = colors.HexColor("#2f8fc0")
FF_BLUE_SOFT = colors.HexColor("#e8f5fc")
FF_RED = colors.HexColor("#E8302A")
FF_TEXT = colors.HexColor("#1A1A2E")
FF_TEXT_MUTED = colors.HexColor("#5b6472")
FF_BORDER = colors.HexColor("#e2e8f0")
FF_GREEN = colors.HexColor("#2E9E5B")
FF_AMBER = colors.HexColor("#F5A623")

_DISCLAIMER = (
    "This report is general agronomic guidance based on Farmers Forward Texas "
    "soil thresholds (Fort Bend County / Brazos River region) and is not a "
    "substitute for a certified agronomist or a full laboratory panel. Verify "
    "before making large purchases. Cost/benefit figures are experimental "
    "ballpark estimates, not financial advice."
)


def _score_hex(score: float) -> colors.Color:
    """Health-score -> brand color per BUILD_CONTRACT §1."""
    s = float(score)
    if s >= 70:
        return FF_GREEN
    if s >= 40:
        return FF_AMBER
    return FF_RED


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles = {
        "brand": ParagraphStyle(
            "ffBrand", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=22, textColor=FF_BLUE, spaceAfter=2, alignment=TA_LEFT),
        "tagline": ParagraphStyle(
            "ffTagline", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, textColor=FF_TEXT_MUTED, spaceAfter=6, alignment=TA_LEFT),
        "h1": ParagraphStyle(
            "ffH1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=16, textColor=FF_TEXT, spaceBefore=10, spaceAfter=6),
        "h2": ParagraphStyle(
            "ffH2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=12, textColor=FF_BLUE_DARK, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle(
            "ffBody", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, textColor=FF_TEXT, leading=14, spaceAfter=4),
        "muted": ParagraphStyle(
            "ffMuted", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, textColor=FF_TEXT_MUTED, leading=12),
        "meta": ParagraphStyle(
            "ffMeta", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, textColor=FF_TEXT, leading=15),
        "score": ParagraphStyle(
            "ffScore", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=40, leading=44, alignment=TA_CENTER),
        "scoreCat": ParagraphStyle(
            "ffScoreCat", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=13, alignment=TA_CENTER),
        "recTitle": ParagraphStyle(
            "ffRecTitle", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=11, textColor=FF_TEXT, spaceBefore=6, spaceAfter=2),
        "coverTitle": ParagraphStyle(
            "ffCoverTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=28, textColor=FF_BLUE, alignment=TA_CENTER, spaceAfter=4),
        "coverSub": ParagraphStyle(
            "ffCoverSub", parent=base["Normal"], fontName="Helvetica",
            fontSize=12, textColor=FF_TEXT_MUTED, alignment=TA_CENTER,
            spaceAfter=2),
    }
    return styles


def _header_block(styles: Dict[str, ParagraphStyle]) -> List:
    """The brand header used at the top of section pages."""
    return [
        Paragraph("Farmers Forward", styles["brand"]),
        Paragraph("Soil Health Report", styles["tagline"]),
        HRFlowable(width="100%", thickness=2, color=FF_BLUE, spaceAfter=10),
    ]


def _inputs_table(inputs: Dict, styles: Dict[str, ParagraphStyle]) -> Table:
    """A branded pH / EC / N / P / K table."""
    def fmt(v, digits):
        try:
            return ("{:." + str(digits) + "f}").format(float(v))
        except (TypeError, ValueError):
            return "-"

    rows = [
        ["Property", "Value", "Units"],
        ["pH", fmt(inputs.get("ph"), 1), ""],
        ["EC (salinity)", fmt(inputs.get("ec"), 2), "dS/m"],
        ["Nitrogen (N)", fmt(inputs.get("nitrogen"), 0), "ppm"],
        ["Phosphorus (P)", fmt(inputs.get("phosphorus"), 0), "ppm"],
        ["Potassium (K)", fmt(inputs.get("potassium"), 0), "ppm"],
    ]
    tbl = Table(rows, colWidths=[2.2 * inch, 1.4 * inch, 1.4 * inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), FF_BLUE_SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), FF_BLUE_DARK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 1), (-1, -1), FF_TEXT),
        ("GRID", (0, 0), (-1, -1), 0.5, FF_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, FF_BLUE_SOFT]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


def _score_block(score, category, styles: Dict[str, ParagraphStyle]) -> Table:
    """A big, color-coded health-score box with its category."""
    color = _score_hex(score)
    try:
        score_txt = "%.0f" % float(score)
    except (TypeError, ValueError):
        score_txt = str(score)

    score_para = Paragraph(
        '<font color="%s">%s</font><font size="14" color="%s">/100</font>'
        % (color.hexval(), score_txt, FF_TEXT_MUTED.hexval()),
        styles["score"])
    cat_para = Paragraph(
        '<font color="%s">%s</font>' % (color.hexval(), str(category or "").upper()),
        styles["scoreCat"])

    box = Table([[score_para], [cat_para]], colWidths=[3.0 * inch])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.5, color),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return box


def _section_flowables(section: Dict, farm_name: str, date: str,
                       styles: Dict[str, ParagraphStyle],
                       with_header: bool = True) -> List:
    """Build the flowables for a single section (reused by both builders)."""
    flow: List = []
    if with_header:
        flow += _header_block(styles)

    label = section.get("section_label", "Section")
    lat = section.get("latitude")
    lng = section.get("longitude")

    flow.append(Paragraph("Field: %s" % (farm_name or "-"), styles["h1"]))
    meta = "Date: %s &nbsp;&nbsp;|&nbsp;&nbsp; Section: <b>%s</b>" % (
        date or "-", label)
    try:
        meta += " &nbsp;&nbsp;|&nbsp;&nbsp; Location: %.6f, %.6f" % (
            float(lat), float(lng))
    except (TypeError, ValueError):
        pass
    flow.append(Paragraph(meta, styles["meta"]))
    flow.append(Paragraph("Crop: <b>%s</b>" % escape(section.get("crop_type") or "Not recorded"), styles["meta"]))
    selected = section.get("selected_crop") or {}
    if selected.get("note"):
        flow.append(Paragraph(escape(selected["note"]), styles["muted"]))
    for factor in selected.get("limiting_factors", []):
        flow.append(Paragraph("&#8226; " + escape(factor), styles["muted"]))
    flow.append(Spacer(1, 8))

    # Score + inputs side by side.
    score = section.get("health_score", 0)
    category = section.get("health_category", "")
    col = [
        Paragraph("Soil Measurements", styles["h2"]),
        _inputs_table(section.get("inputs", {}), styles),
    ]
    two = Table(
        [[col, _score_block(score, category, styles)]],
        colWidths=[5.0 * inch, 3.2 * inch])
    two.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow.append(two)
    flow.append(Spacer(1, 12))

    # Crop viability.
    flow.append(Paragraph("Crops You Can Plant Now", styles["h2"]))
    crop = section.get("crop_suitability", {}) or {}
    viable = crop.get("viable_now", []) or []
    if viable:
        flow.append(Paragraph(", ".join(viable), styles["body"]))
    else:
        flow.append(Paragraph(
            "None viable under current conditions - address the action items "
            "below and re-test.", styles["muted"]))
    flow.append(Spacer(1, 8))

    # Primary problem (context for the recommendations).
    primary = section.get("primary_problem") or {}
    if primary.get("code") and primary.get("code") != "none":
        flow.append(Paragraph("Primary Problem", styles["h2"]))
        flow.append(Paragraph(
            "<b>%s</b> (%s): %s" % (
                primary.get("label", ""), primary.get("severity", ""),
                primary.get("message", "")),
            styles["body"]))
        flow.append(Spacer(1, 6))

    # Action items / recommendations.
    flow.append(Paragraph("Action Items", styles["h2"]))
    recs = section.get("recommendations", []) or []
    if not recs:
        flow.append(Paragraph(
            "No corrective action needed right now - keep monitoring pH, "
            "salinity and N/P/K.", styles["body"]))
    for i, r in enumerate(recs, 1):
        flow.append(Paragraph("%d. %s" % (i, r.get("title", "Recommendation")),
                              styles["recTitle"]))
        if r.get("action"):
            flow.append(Paragraph(r["action"], styles["body"]))
        if r.get("notes"):
            flow.append(Paragraph(escape(r["notes"]), styles["muted"]))
        detail = []
        if r.get("rate"):
            detail.append("<b>Rate:</b> %s" % r["rate"])
        if r.get("cost_estimate"):
            detail.append("<b>Cost:</b> %s" % r["cost_estimate"])
        if r.get("timing"):
            detail.append("<b>When:</b> %s" % r["timing"])
        if detail:
            flow.append(Paragraph(" &nbsp;&bull;&nbsp; ".join(detail),
                                  styles["muted"]))
        flow.append(Spacer(1, 4))

    flow.append(Spacer(1, 10))
    flow.append(HRFlowable(width="100%", thickness=0.75, color=FF_BORDER,
                           spaceAfter=6))
    flow.append(Paragraph(_DISCLAIMER, styles["muted"]))
    return flow


def _footer(canvas, doc):
    """Brand footer drawn on every page."""
    canvas.saveState()
    canvas.setStrokeColor(FF_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.6 * inch, letter[0] - 0.75 * inch, 0.6 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(FF_TEXT_MUTED)
    canvas.drawString(0.75 * inch, 0.42 * inch,
                      "Farmers Forward - free soil health assessments for "
                      "small-scale Texas farmers")
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.42 * inch,
                           "Page %d" % doc.page)
    canvas.restoreState()


def _render(flowables: List) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.85 * inch,
        title="Farmers Forward Soil Health Report", author="Farmers Forward")
    doc.build(flowables, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------
def build_section_pdf(section: Dict, farm_name: str, date: str) -> bytes:
    """Render a single section's report to PDF bytes."""
    styles = _styles()
    flow = _section_flowables(section, farm_name, date, styles, with_header=True)
    return _render(flow)


def build_combined_pdf(analysis: Dict) -> bytes:
    """Render a cover page + one page per section to PDF bytes."""
    styles = _styles()
    farm_name = analysis.get("farm_name", "-")
    date = analysis.get("date", "-")
    summary = analysis.get("field_summary", {}) or {}
    results = analysis.get("results", []) or []

    flow: List = []

    # --- cover page -------------------------------------------------------
    flow.append(Spacer(1, 1.4 * inch))
    flow.append(Paragraph("Farmers Forward", styles["coverTitle"]))
    flow.append(Paragraph("Soil Health Report", styles["coverSub"]))
    flow.append(HRFlowable(width="55%", thickness=2, color=FF_BLUE,
                           spaceBefore=10, spaceAfter=18, hAlign="CENTER"))
    flow.append(Paragraph("<b>%s</b>" % farm_name, styles["coverTitle"]))
    flow.append(Paragraph("Sampled %s" % date, styles["coverSub"]))
    flow.append(Spacer(1, 0.4 * inch))

    mean_score = summary.get("mean_health_score", 0)
    flow.append(_score_block(mean_score, summary.get("health_category", ""),
                             styles))
    flow.append(Spacer(1, 0.3 * inch))

    def _num(v, digits=0):
        try:
            return ("{:." + str(digits) + "f}").format(float(v))
        except (TypeError, ValueError):
            return "-"

    summary_rows = [
        ["Field summary", ""],
        ["Sections analyzed", str(summary.get("n_sections", len(results)))],
        ["Mean health score", "%s / 100" % _num(mean_score, 0)],
        ["Health category", str(summary.get("health_category", "-"))],
        ["Score range", "%s to %s" % (_num(summary.get("min_health_score"), 0),
                                       _num(summary.get("max_health_score"), 0))],
        ["Dominant problem", str(summary.get("dominant_problem_label", "-"))],
    ]
    stbl = Table(summary_rows, colWidths=[2.6 * inch, 3.0 * inch], hAlign="CENTER")
    stbl.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), FF_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 1), (-1, -1), FF_TEXT),
        ("GRID", (0, 0), (-1, -1), 0.5, FF_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, FF_BLUE_SOFT]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    flow.append(stbl)

    # --- one section per page --------------------------------------------
    for section in results:
        flow.append(PageBreak())
        flow += _header_block(styles)
        flow += _section_flowables(section, farm_name, date, styles,
                                   with_header=False)

    return _render(flow)
