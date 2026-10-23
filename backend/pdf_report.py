"""Brand-styled PDF report generation for Farmers Forward (ReportLab).

Two public builders:
  * build_section_pdf(section, farm_name, date) -> bytes   (one section)
  * build_combined_pdf(analysis, map_image=None) -> bytes  (cover, field map,
    all sections)

Both return raw PDF bytes rendered via SimpleDocTemplate into a BytesIO buffer.
Colors come from the brand tokens in BUILD_CONTRACT §1.
"""

from __future__ import annotations

import base64
import binascii
import io
from html import escape
from typing import Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend import fert_pdf

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
    "before making large purchases."
)


def _score_hex(score: float) -> colors.Color:
    """Health-score -> brand color per BUILD_CONTRACT §1."""
    s = float(score)
    if s >= 70:
        return FF_GREEN
    if s >= 40:
        return FF_AMBER
    return FF_RED


def _decode_data_url(data_url: str) -> Optional[bytes]:
    """Raw bytes from a `data:image/<type>;base64,...` URL, or None if unusable.

    The value comes straight from the browser, so every malformed shape has to
    fall through to None rather than raise: a bad image must cost the report
    its map, never the whole download.
    """
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        return None
    _, _, encoded = data_url.partition(",")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None


def _map_flowables(map_image: Optional[str], avail_width: float,
                   styles: Dict[str, ParagraphStyle]) -> List:
    """A scaled field-map image plus caption, or [] when there is no usable one.

    `map_image` is the results map as the user saw it, rasterised in the
    browser (score-coloured cells, pins and legend included) and posted up with
    the report request.
    """
    raw = _decode_data_url(map_image or "")
    if not raw:
        return []

    try:
        reader = ImageReader(io.BytesIO(raw))
        src_w, src_h = reader.getSize()
    except Exception:  # noqa: BLE001 - any unreadable image just loses the map
        return []
    if not src_w or not src_h:
        return []

    # Fit the width, then cap the height so the map can never push the cover
    # page's summary table onto a second page.
    width = avail_width
    height = width * (float(src_h) / float(src_w))
    max_height = 3.6 * inch
    if height > max_height:
        height = max_height
        width = height * (float(src_w) / float(src_h))

    img = Image(io.BytesIO(raw), width=width, height=height)
    img.hAlign = "CENTER"
    return [
        img,
        Spacer(1, 4),
        Paragraph(
            "Sampling grid and soil health colours as shown when this report "
            "was generated.",
            styles["muted"]),
    ]


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

    ec_method = inputs.get("ec_method")
    rows = [
        ["Property", "Value", "Units"],
        ["pH", fmt(inputs.get("ph"), 1), ""],
        ["EC (%s soil:water)" % ec_method if ec_method else "EC (salinity)",
         fmt(inputs.get("ec"), 2), "dS/m"],
    ]
    if inputs.get("ece") is not None:  # older saved results have no ECe
        rows.append(["ECe (est. saturated paste)", fmt(inputs["ece"], 2), "dS/m"])
    rows += [
        ["Nitrogen (N)", fmt(inputs.get("nitrogen"), 0), "ppm"],
        ["Phosphorus (P)", fmt(inputs.get("phosphorus"), 0), "ppm"],
        ["Potassium (K)", fmt(inputs.get("potassium"), 0), "ppm"],
    ]
    tbl = Table(rows, colWidths=[2.0 * inch, 0.9 * inch, 0.9 * inch])
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

    box = Table([[score_para], [cat_para]], colWidths=[2.8 * inch])
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
    if selected.get("basis"):
        flow.append(Paragraph(escape(selected["basis"]), styles["muted"]))
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
    score_col = [_score_block(score, category, styles)]
    if section.get("score_basis") == "crop":
        crop_name = (selected.get("profile_name") or section.get("crop_type") or "").lower()
        score_col += [Spacer(1, 6), Paragraph(
            "Score is for %s. General soil health: <b>%.0f</b>/100 (%s)." % (
                escape(crop_name), float(section.get("general_health_score") or 0),
                escape(section.get("general_health_category") or "-")),
            styles["muted"])]
    two = Table(  # 7.0 in = the letter page inside 0.75 in margins
        [[col, score_col]],
        colWidths=[4.1 * inch, 2.9 * inch])
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
    if viable and "ideal" in crop:
        for heading, names in (("Ideal", crop.get("ideal") or []),
                               ("Viable with adjustments", crop.get("viable_with_adjustments") or [])):
            if names:
                flow.append(Paragraph("<b>%s:</b> %s" % (heading, escape(", ".join(names))),
                                      styles["body"]))
    elif viable:  # results saved before the ideal / with-adjustments split
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
                escape(primary.get("label", "")), escape(primary.get("severity", "")),
                escape(primary.get("message", ""))),
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
            detail.append("<b>Amount:</b> %s" % escape(r["rate"]))
        if r.get("timing"):
            detail.append("<b>When:</b> %s" % escape(r["timing"]))
        if detail:
            flow.append(Paragraph(" &nbsp;&bull;&nbsp; ".join(detail),
                                  styles["muted"]))
        flow.append(Spacer(1, 4))

    flow += fert_pdf.section_flowables(section, styles)

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


def build_combined_pdf(analysis: Dict, map_image: Optional[str] = None) -> bytes:
    """Render a cover page + field map + one page per section to PDF bytes.

    `map_image` is an optional `data:image/...;base64,...` of the results map,
    captured in the browser. When it is missing or unreadable the report is
    built exactly as before, just without the map page.
    """
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

    # --- field map, on its own page so it can be full width ---------------
    # Page width less the 0.75" margins SimpleDocTemplate is built with below.
    avail_width = letter[0] - 1.5 * inch
    map_flow = _map_flowables(map_image, avail_width, styles)
    if map_flow:
        flow.append(PageBreak())
        flow += _header_block(styles)
        flow.append(Paragraph("Field Map", styles["h1"]))
        flow.append(Paragraph(
            "Sample points and their grid cells, coloured by soil health "
            "score: red for poor, amber for moderate, green for good.",
            styles["body"]))
        flow.append(Spacer(1, 8))
        flow += map_flow

    # --- one section per page --------------------------------------------
    for section in results:
        flow.append(PageBreak())
        flow += _header_block(styles)
        flow += _section_flowables(section, farm_name, date, styles,
                                   with_header=False)

    flow += fert_pdf.field_total_flowables(analysis, styles)

    return _render(flow)
