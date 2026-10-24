"""Dual-format report builders: structured JSON (for the future app) and a
plain-English report a farmer can read on-site.

Both consume the single assembled `field` dict built by predict.py. Reporter
only formats -- it computes nothing agronomic.
"""

from __future__ import annotations

import json
import textwrap
from typing import Dict, List

import numpy as np

# Short, human headline for each problem code.
PROBLEM_LABELS = {
    "ph_acidic": "Acidic soil (low pH)",
    "ph_alkaline": "Alkaline soil (high pH)",
    "salinity": "High salinity (salt)",
    "nitrogen_deficient": "Nitrogen deficiency",
    "phosphorus_deficient": "Phosphorus deficiency",
    "phosphorus_excess": "Excess phosphorus (runoff risk)",
    "potassium_deficient": "Potassium deficiency",
    "none": "No major problem detected",
}


def problem_label(code: str) -> str:
    return PROBLEM_LABELS.get(code, code.replace("_", " ").title())


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def _json_safe(obj):
    """Recursively coerce numpy / pandas scalars to plain Python for json.dump."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return _json_safe(obj.tolist())
    return obj


def build_json_report(field: Dict) -> Dict:
    """Assemble the machine-readable report structure."""
    report = {
        "meta": {
            "tool": "FarmersFirst Soil Health Pipeline",
            "version": "0.1.0",
            "generated_at": field.get("generated_at"),
            "field_name": field.get("field_name"),
            "disclaimer": "Recommendations are general agronomic guidance based "
                          "on Texas thresholds, not a substitute for a certified "
                          "agronomist or lab. Verify before large purchases.",
        },
        "field_summary": field["field_summary"],
        "soil_profile": field.get("field_soil_profile"),
        "field_recommendations": field["field_recommendations"],
        "crop_suitability": field["field_crop_suitability"],
        "cost_benefit_experimental": field["field_cost_benefit"],
        "zones": field["zones"],
        "samples": field["samples"],
        "artifacts": {
            "field_map_png": field.get("map_path"),
        },
    }
    return _json_safe(report)


def write_json_report(field: Dict, path: str) -> str:
    with open(path, "w") as fh:
        json.dump(build_json_report(field), fh, indent=2)
    return path


# ---------------------------------------------------------------------------
# Plain-English
# ---------------------------------------------------------------------------
_WIDTH = 78


def _wrap(text: str, indent: str = "") -> str:
    return textwrap.fill(text, width=_WIDTH,
                         initial_indent=indent, subsequent_indent=indent)


def _rule(char="=") -> str:
    return char * _WIDTH


def build_text_report(field: Dict) -> str:
    fs = field["field_summary"]
    L: List[str] = []
    L.append(_rule("="))
    L.append("FARMERSFIRST  --  SOIL HEALTH REPORT".center(_WIDTH))
    L.append(("Field: %s" % field.get("field_name", "Unnamed")).center(_WIDTH))
    L.append(_rule("="))
    L.append("")
    L.append("Generated: %s" % field.get("generated_at", ""))
    L.append("Samples: %d   |   Zones: %d" % (field["n_samples"], field["n_zones"]))
    L.append("")

    # --- overall ----------------------------------------------------------
    L.append(_rule("-"))
    L.append("OVERALL SOIL HEALTH")
    L.append(_rule("-"))
    L.append("  Field score : %.0f / 100   (%s)"
             % (fs["mean_health_score"], fs["health_category"]))
    L.append("  Range across field : %.0f (worst zone) to %.0f (best)"
             % (fs["min_health_score"], fs["max_health_score"]))
    L.append(_wrap("Most common problem across the field: %s."
                   % problem_label(fs["dominant_problem_code"]), "  "))
    L.append("")

    # --- soil profile (the five measured properties, field averages) ------
    sp = field.get("field_soil_profile")
    if sp:
        L.append(_rule("-"))
        L.append("SOIL PROFILE  (field averages)")
        L.append(_rule("-"))
        L.append("  pH        : %.1f" % sp["ph"])
        L.append("  EC        : %.2f dS/m (salinity)" % sp["ec"])
        L.append("  Nitrogen  : %.0f ppm" % sp["nitrogen"])
        L.append("  Phosphorus: %.0f ppm" % sp["phosphorus"])
        L.append("  Potassium : %.0f ppm" % sp["potassium"])
        L.append("")

    # --- what to do -------------------------------------------------------
    L.append(_rule("-"))
    L.append("WHAT TO DO  (most important first)")
    L.append(_rule("-"))
    recs = field["field_recommendations"]
    if not recs:
        L.append(_wrap("No corrective action needed right now -- keep monitoring "
                       "pH, salinity and N/P/K, and maintain fertility with cover "
                       "crops.", "  "))
    for i, r in enumerate(recs, 1):
        L.append("")
        L.append("  %d. %s  [%s]" % (i, r["title"], r["severity"].upper()))
        L.append(_wrap(r["action"], "     "))
        L.append("     Amount: %s" % r["rate"])
        L.append("     When  : %s" % r["timing"])
        if r.get("notes"):
            L.append(_wrap("Note  : %s" % r["notes"], "     "))
        L.append("     Affects: %d of %d zones"
                 % (r.get("affected_zones", 0), field["n_zones"]))
    L.append("")

    # --- crops ------------------------------------------------------------
    cs = field["field_crop_suitability"]
    L.append(_rule("-"))
    L.append("WHAT YOU CAN PLANT NOW  (given current conditions)")
    L.append(_rule("-"))
    L.append(_wrap("Viable across the WHOLE field: %s"
                   % (", ".join(cs["viable_everywhere"]) or "none"), "  "))
    if cs["viable_in_some_zones"]:
        parts = ["%s (%d zone%s)" % (c["crop"], c["n_zones"],
                                     "" if c["n_zones"] == 1 else "s")
                 for c in cs["viable_in_some_zones"]]
        L.append(_wrap("Viable in SOME zones only: %s" % ", ".join(parts), "  "))
    L.append(_wrap("Not viable anywhere yet: %s"
                   % (", ".join(cs["not_viable_anywhere"]) or "none"), "  "))
    L.append(_wrap("(After the fixes above, salt/pH-sensitive crops open up. "
                   "Fix salinity and nutrients first, then re-test.)", "  "))
    L.append("")

    # --- zones ------------------------------------------------------------
    L.append(_rule("-"))
    L.append("ZONE-BY-ZONE")
    L.append(_rule("-"))
    L.append("  %-8s %6s  %-10s %-22s %s"
             % ("Zone", "Score", "Category", "Main problem", "Viable crops"))
    for z in field["zones"]:
        L.append("  %-8s %6.0f  %-10s %-22s %d"
                 % (z["zone"], z["mean_health_score"], z["health_category"],
                    problem_label(z["dominant_problem"])[:22], z["n_viable_crops"]))
    L.append("")

    # --- experimental cost/benefit ---------------------------------------
    cb = field["field_cost_benefit"]
    L.append(_rule("-"))
    L.append("[EXPERIMENTAL] ROUGH COST / BENEFIT  --  NOT FINANCIAL ADVICE")
    L.append(_rule("-"))
    L.append(_wrap(cb["disclaimer"], "  "))
    L.append("")
    L.append("  Est. total cost to implement fixes : %s"
             % cb["total_est_cost_per_acre"])
    L.append("  Est. crop value protected per year : %s"
             % cb["total_est_value_protected_per_acre"])
    L.append("  Rough first-year net (ballpark)     : %s"
             % cb["rough_first_year_net_per_acre"])
    L.append("")

    # --- footer -----------------------------------------------------------
    L.append(_rule("="))
    if field.get("map_path"):
        L.append("Field map image: %s" % field["map_path"])
    L.append(_wrap("This report uses FarmersFirst Texas soil thresholds (Fort "
                   "Bend / Brazos region). It is general agronomic guidance, not "
                   "a substitute for a certified agronomist or a full lab panel. "
                   "FarmersFirst -- free soil health assessments for small-scale "
                   "Texas farmers."))
    L.append(_rule("="))
    return "\n".join(L)


def write_text_report(field: Dict, path: str) -> str:
    with open(path, "w") as fh:
        fh.write(build_text_report(field))
    return path
