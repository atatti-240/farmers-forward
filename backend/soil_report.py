"""The Soil Profile page of the combined PDF report (ReportLab).

Mapped USDA soil-survey data (SSURGO) for one point in the field: the soil
series, its typical surface organic matter and a few soil properties. It is
display only and never feeds the health score. The lookup itself lives in
soil_info; this module only lays out what /api/submit stored in the analysis
as `soil_point` and `soil_profile`.

One public builder:
  * soil_profile_flowables(analysis, styles, avail_width) -> List
    ([] when the analysis has no soil_point, so older reports are unchanged)
"""

from __future__ import annotations

import logging
import math
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from typing import Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from backend import pdf_report

log = logging.getLogger(__name__)

_FALLBACK_NOTE = ("Mapped soil survey data (USDA NRCS SSURGO), not a measurement "
                  "of this field.")
_LAB_NOTE = "Send a sample to a soil lab to measure this field's actual organic matter."
_NOT_LOADED = ("Soil survey data had not loaded for this field when this report "
               "was made. Open the results page and download the report again.")
_RETRY_NOTE = "To fill in the gaps, open the results page and download the report again."

# Hydrologic soil groups: how readily water soaks in once the soil is wet.
_HYDRO_HINTS = {
    "A": "high infiltration, low runoff potential",
    "B": "moderate infiltration",
    "C": "slow infiltration",
    "D": "very slow infiltration, high runoff potential",
}
# Dual groups (e.g. "C/D") behave like the first letter only where drained.
_HYDRO_SHORT = {"A": "high", "B": "moderate", "C": "slow", "D": "very slow"}


def _num(value, digits: int = 0) -> Optional[str]:
    """`value` rounded half-up to `digits` decimals, or None if not a number."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    step = Decimal(1).scaleb(-digits)
    return str(Decimal(number).quantize(step, rounding=ROUND_HALF_UP))


def _text(value) -> Optional[str]:
    """A trimmed, non-empty string, or None."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _point(point) -> Optional[Tuple[float, float]]:
    """(lat, lon) from a stored soil_point, or None if it is unusable."""
    if not isinstance(point, dict):
        return None
    try:
        lat, lon = float(point.get("lat")), float(point.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    return lat, lon


def _hydro_text(group: str) -> str:
    """'D' -> 'D: very slow infiltration, high runoff potential'."""
    key = group.upper()
    if key in _HYDRO_HINTS:
        return "%s: %s" % (group, _HYDRO_HINTS[key])
    drained, _, undrained = key.partition("/")
    if drained in _HYDRO_SHORT and undrained in _HYDRO_SHORT:
        return "%s: %s infiltration if drained, %s if not" % (
            group, _HYDRO_SHORT[drained], _HYDRO_SHORT[undrained])
    return group


def _layer(profile: Dict) -> Optional[str]:
    """'0-29 cm' for the surface layer the values describe, or None."""
    top = _num(profile.get("layer_top_cm"), 0)
    bottom = _num(profile.get("layer_bottom_cm"), 0)
    if top is None or bottom is None:
        return None
    return "%s-%s cm" % (top, bottom)


def _property_rows(profile: Dict, with_layer: bool) -> List[Tuple[str, str]]:
    """(label, plain text) pairs for the details table; empty values omitted."""
    rows: List[Tuple[str, str]] = []

    series = _text(profile.get("series"))
    if series:
        pct = _num(profile.get("component_pct"), 0)
        rows.append(("Soil series", "%s (%s%% of map unit)" % (series, pct)
                     if pct is not None else series))
    for label, key in (("Map unit", "map_unit"), ("Survey area", "survey_area")):
        value = _text(profile.get(key))
        if value:
            rows.append((label, value))

    texture = _text(profile.get("texture"))
    fractions = [(_num(profile.get(key), 0), name) for key, name in
                 (("sand_pct", "sand"), ("silt_pct", "silt"), ("clay_pct", "clay"))]
    fractions_text = ", ".join("%s%% %s" % (pct, name)
                               for pct, name in fractions if pct is not None)
    if texture and fractions_text:
        rows.append(("Surface texture", "%s: %s" % (texture, fractions_text)))
    elif texture or fractions_text:
        rows.append(("Surface texture", texture or fractions_text))
    layer = _layer(profile)
    if with_layer and layer:
        rows.append(("Surface layer", layer))

    drainage = _text(profile.get("drainage"))
    if drainage:
        rows.append(("Drainage", drainage))
    group = _text(profile.get("hydrologic_group"))
    if group:
        # The infiltration hint describes a soil; a "Water" or "Urban land"
        # map unit (no_data) can still carry a group, so it gets the bare letter.
        rows.append(("Hydrologic group", _hydro_text(group)
                     if profile.get("status") == "ok" else group))
    cec = _num(profile.get("cec"), 1)
    if cec is not None:
        rows.append(("CEC", "%s meq/100 g" % cec))
    return rows


def _callout(paragraphs: List, avail_width: float, border) -> Table:
    """A boxed, full-width panel holding the page's headline paragraphs."""
    box = Table([[p] for p in paragraphs], colWidths=[avail_width])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, border),
        ("BACKGROUND", (0, 0), (-1, -1), pdf_report.FF_BLUE_SOFT),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    return box


def _properties_table(rows: List[Tuple[str, str]], styles: Dict[str, ParagraphStyle],
                      avail_width: float) -> Table:
    """The two-column soil details table."""
    label_style = ParagraphStyle("ffSoilLabel", parent=styles["body"],
                                 fontName="Helvetica-Bold", spaceAfter=0)
    value_style = ParagraphStyle("ffSoilValue", parent=styles["body"], spaceAfter=0)
    label_width = 1.8 * inch
    tbl = Table(
        [[Paragraph(escape(label), label_style), Paragraph(escape(value), value_style)]
         for label, value in rows],
        colWidths=[label_width, avail_width - label_width])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, pdf_report.FF_BORDER),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, pdf_report.FF_BLUE_SOFT]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


def _headline(profile: Optional[Dict], styles: Dict[str, ParagraphStyle],
              avail_width: float) -> Tuple[Table, bool]:
    """The callout for this profile, and whether it already names the layer."""
    head = ParagraphStyle("ffSoilHeadline", parent=styles["body"],
                          fontName="Helvetica-Bold", fontSize=12, leading=16,
                          spaceAfter=2)
    muted = styles["muted"]

    if not isinstance(profile, dict):
        return _callout([Paragraph(escape(_NOT_LOADED), styles["body"])],
                        avail_width, pdf_report.FF_AMBER), False

    status = profile.get("status")
    reason = _text(profile.get("reason"))
    om = _num(profile.get("organic_matter_pct"), 1)

    if status == "ok" and om is not None:
        lines = [Paragraph(
            'Typical organic matter for this soil type: <font color="%s">%s%%</font>'
            % (pdf_report.FF_BLUE_DARK.hexval(), escape(om)), head)]
        low = _num(profile.get("organic_matter_low"), 1)
        high = _num(profile.get("organic_matter_high"), 1)
        layer = _layer(profile)
        detail = []
        if low is not None and high is not None:
            detail.append("survey range %s-%s%%" % (low, high))
        if layer:
            detail.append("top layer %s" % layer)
        if detail:
            lines.append(Paragraph(escape("(%s)" % ", ".join(detail)), muted))
        return _callout(lines, avail_width, pdf_report.FF_BLUE), bool(layer)

    if status == "ok":  # a real soil whose survey records no surface organic matter
        title = "Typical organic matter for this soil type: not recorded"
        notes = [reason]
    elif status == "no_data":
        title = "No soil survey data for this point"
        notes = [reason]
    elif status == "partial":
        title = "Some soil survey data is missing"
        notes = [reason, _RETRY_NOTE]
    else:  # "unavailable", or anything unexpected in a stored report
        title = "Soil survey data unavailable"
        notes = [reason or "The soil survey services did not respond in time.",
                 _RETRY_NOTE]
    lines = [Paragraph(escape(title), head)]
    lines += [Paragraph(escape(note), styles["body"]) for note in notes if note]
    border = pdf_report.FF_BLUE if status == "ok" else pdf_report.FF_AMBER
    return _callout(lines, avail_width, border), False


def _build(analysis: Dict, styles: Dict[str, ParagraphStyle],
           avail_width: float) -> List:
    coords = _point(analysis.get("soil_point"))
    if coords is None:
        return []
    profile = analysis.get("soil_profile")

    flow: List = [
        Paragraph("Soil Profile", styles["h1"]),
        Paragraph(
            "USDA soil survey at %.4f, %.4f (the field centre when a boundary was "
            "drawn, otherwise the farm pin or first sample point)." % coords,
            styles["body"]),
        Spacer(1, 6),
    ]

    callout, names_layer = _headline(profile, styles, avail_width)
    flow.append(callout)

    rows = _property_rows(profile, with_layer=not names_layer) \
        if isinstance(profile, dict) else []
    if rows:
        flow.append(Paragraph("Survey details", styles["h2"]))
        flow.append(_properties_table(rows, styles, avail_width))

    note = _text(profile.get("source_note")) if isinstance(profile, dict) else None
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(escape(note or _FALLBACK_NOTE), styles["muted"]))
    # Only beside soil values, as on the results page; not for open water.
    if isinstance(profile, dict) and profile.get("status") in ("ok", "partial"):
        flow.append(Spacer(1, 4))
        flow.append(Paragraph(escape(_LAB_NOTE), styles["muted"]))
    return flow


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------
def soil_profile_flowables(analysis: Dict, styles: Dict[str, ParagraphStyle],
                           avail_width: float) -> List:
    """The Soil Profile page's flowables, or [] when there is nothing to show.

    [] when the analysis has no soil_point: reports saved before this feature
    (and submissions without a mapped point) keep their exact page count. The
    stored profile is display data from outside services, so anything odd in it
    costs the report this page, never the whole download.
    """
    if not isinstance(analysis, dict):
        return []
    try:
        return _build(analysis, styles, avail_width)
    except Exception:  # noqa: BLE001 - a bad stored profile just loses its page
        log.exception("soil profile page skipped")
        return []
