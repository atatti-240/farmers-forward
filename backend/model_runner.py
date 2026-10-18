"""Run the trained soil-health pipeline on submitted field sections.

This wraps the FarmersFirst ML/agronomy pipeline (in
`backend/FarmersForwardModel/`) with a single, app-friendly entry point
`run_analysis(...)`. The joblib model bundle is loaded once and cached at module
level; the analysis mirrors `predict.py::analyze_sample` (ML headline score +
rule-based problem detection, recommendations, and crop viability).
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

from backend import config

# NOTE: the heavy scientific stack (pandas, scikit-learn, xgboost, joblib) and the
# `pipeline` package are imported LAZILY inside the functions that need them, NOT
# at module import time. Importing this module (which `app.py` does at startup)
# must stay cheap so the gunicorn worker doesn't balloon in memory and get OOM-
# killed on light endpoints like /api/auth/login. The ML stack loads only on the
# first /api/submit.

MODEL_PATH = os.path.join(config.MODEL_DIR, "models", "soil_models.joblib")


def _ensure_pipeline_path() -> None:
    """Put the FarmersForwardModel package dir on sys.path (idempotent)."""
    if config.MODEL_DIR not in sys.path:
        sys.path.insert(0, config.MODEL_DIR)

# Brand health-score -> color rule (kept identical to BUILD_CONTRACT §1).
_GREEN = "#2E9E5B"
_AMBER = "#F5A623"
_RED = "#E8302A"

_BUNDLE = None  # lazy module-level cache


def _get_bundle():
    """Load the joblib bundle once and reuse it for every request."""
    global _BUNDLE
    if _BUNDLE is None:
        _ensure_pipeline_path()
        from pipeline import model as M  # heavy: scikit-learn / xgboost / joblib
        _BUNDLE = M.load_bundle(MODEL_PATH)
    return _BUNDLE


def score_color(score: float) -> Dict[str, str]:
    """Map a 0-100 health score to a {'bucket', 'hex'} color per BUILD_CONTRACT §1.

    score >= 70 -> green; 40 <= score < 70 -> amber (yellow bucket); else red.
    """
    s = float(score)
    if s >= 70:
        return {"bucket": "green", "hex": _GREEN}
    if s >= 40:
        return {"bucket": "yellow", "hex": _AMBER}
    return {"bucket": "red", "hex": _RED}


def _measurements(section: Dict) -> Dict[str, float]:
    """The five soil measurements as plain floats, with EC as ECe.

    The sensor reads a 1:2 soil:water extract (T.SENSOR_EC_METHOD), while the
    ML model was trained on ECe and every EC threshold is ECe, so the reading is
    converted here, once, for live scoring and full analysis alike.
    """
    _ensure_pipeline_path()
    from pipeline import crop_profile  # light (two JSON tables)
    from pipeline import thresholds as T  # light (plain dicts)
    m = {c: float(section[c]) for c in T.MEASUREMENT_COLUMNS}
    m["ec"] = crop_profile.to_ece(m["ec"])
    return m


def _predict(sections: List[Dict], prefix) -> List[Dict]:
    """Run the ML models over `sections`, returning one prediction per section."""
    _ensure_pipeline_path()
    import pandas as pd
    from pipeline import model as M
    from pipeline import preprocess

    rows = []
    for sec in sections:
        label = sec["section_label"]
        m = _measurements(sec)
        rows.append({
            "sample_id": "%s-%s" % (prefix, label),
            "latitude": float(sec.get("latitude", 0.0)),
            "longitude": float(sec.get("longitude", 0.0)),
            "region": "fort_bend",
            "zone": label,
            "ph": m["ph"],
            "ec": m["ec"],
            "nitrogen": m["nitrogen"],
            "phosphorus": m["phosphorus"],
            "potassium": m["potassium"],
        })
    features = preprocess.build_features(pd.DataFrame(rows))
    return M.predict_frame(_get_bundle(), features)


def _headline_score(m: Dict[str, float], crop, ml: Dict) -> Dict:
    """The score a section is shown and coloured by.

    For a crop with a Texas A&M table that is the crop-specific rule score;
    otherwise (no crop, or a custom/unmapped one) it is the ML score. The ML
    score is always kept as the "general soil health" score.
    """
    from pipeline import agronomy
    from pipeline import thresholds as T
    general = round(float(ml["ml_health_score"]), 1)
    out = {"general_health_score": general,
           "general_health_category": ml["ml_category_from_score"]}
    if crop is not None and crop.mapped:
        score = round(float(agronomy.compute_health_score(m, crop)), 1)
        out.update(health_score=score, health_category=T.categorize(score), score_basis="crop")
    else:
        out.update(health_score=general, health_category=out["general_health_category"],
                   score_basis="general")
    return out


def score_sections(sections: List[Dict]) -> List[Dict]:
    """Health score only, for live map colouring while data is being entered.

    Uses the same headline score as `run_analysis` (crop-specific when the
    optional `crop_type` maps to an A&M table, else ML), so a point keeps its
    colour when the field is submitted. Each section needs `section_label` and
    the five measurements; returns `[{section_label, health_score, health_category}]`.
    """
    _ensure_pipeline_path()
    from pipeline import crop_profile
    out = []
    for sec, ml in zip(sections, _predict(sections, "F")):
        score = _headline_score(_measurements(sec), crop_profile.resolve(sec.get("crop_type")), ml)
        out.append({"section_label": sec["section_label"],
                    "health_score": score["health_score"],
                    "health_category": score["health_category"]})
    return out


def run_analysis(farm_name: str, date: str, sections: List[Dict],
                 farm_id: Optional[int] = None) -> Dict:
    """Analyze every section and return the full field report.

    `sections` is a list of dicts with keys: section_label, latitude, longitude,
    crop_type, ph, ec, nitrogen, phosphorus, potassium. Returns the structure defined in
    BUILD_CONTRACT §2.5. Each section's crop_type selects the Texas A&M table its
    pH, salinity and N/P/K are judged against (pipeline.crop_profile).
    """
    # Lazy heavy imports (see module note): only paid on the first submission.
    _ensure_pipeline_path()
    from pipeline import agronomy
    from pipeline import crop_profile
    from pipeline import recommender
    from pipeline import reporter
    from pipeline import thresholds as T

    prefix = farm_id if farm_id is not None else "F"
    ml_results = _predict(sections, prefix)

    results: List[Dict] = []
    scores: List[float] = []
    problem_codes: List[str] = []

    for sec, ml in zip(sections, ml_results):
        label = sec["section_label"]
        m = _measurements(sec)
        crop_type = sec.get("crop_type")
        crop = crop_profile.resolve(crop_type)

        problems = agronomy.detect_problems(m, crop)
        primary = agronomy.primary_problem(m, crop)
        rule_score = agronomy.compute_health_score(m, crop)
        viability = agronomy.crop_viability(m)
        recs = recommender.build_recommendations(m, problems, target_crop=crop)
        selected_crop = recommender.assess_target_crop(m, crop)
        cost_benefit = recommender.estimate_cost_benefit(m, recs)

        score = _headline_score(m, crop, ml)
        scores.append(score["health_score"])
        if primary["code"] != "none":
            problem_codes.append(primary["code"])

        results.append({
            "section_label": label,
            "crop_type": crop_type,
            "selected_crop": selected_crop,
            "sample_id": "%s-%s" % (prefix, label),
            "latitude": float(sec["latitude"]),
            "longitude": float(sec["longitude"]),
            # As entered (EC on the sensor's basis), plus the ECe used for analysis.
            "inputs": dict({c: round(float(sec[c]), 2) for c in T.MEASUREMENT_COLUMNS},
                           ece=round(m["ec"], 2), ec_method=T.SENSOR_EC_METHOD),
            "crop_assessment": crop_profile.assess(m, crop),
            "health_score": score["health_score"],
            "health_category": score["health_category"],
            "score_basis": score["score_basis"],  # "crop" or "general"
            "general_health_score": score["general_health_score"],
            "general_health_category": score["general_health_category"],
            "rule_health_score": round(float(rule_score), 1),
            "color": score_color(score["health_score"]),
            "primary_problem": {
                "code": primary["code"],
                "label": reporter.problem_label(primary["code"]),
                "severity": primary["severity"],
                "message": primary["message"],
            },
            "problems": problems,
            "recommendations": recs,
            "crop_suitability": {
                "viable_now": viability["viable"],          # ideal + with adjustments
                "ideal": viability["ideal"],
                "viable_with_adjustments": viability["with_adjustments"],
                "not_viable": viability["not_viable"],
            },
            "ml_crop_suitable": ml["ml_crop_suitable"],
            "cost_benefit_experimental": cost_benefit,
        })

    n_sections = len(results)
    mean_score = round(sum(scores) / n_sections, 1) if n_sections else 0.0
    if problem_codes:
        dominant = max(set(problem_codes), key=problem_codes.count)
    else:
        dominant = "none"

    field_summary = {
        "mean_health_score": mean_score,
        "health_category": T.categorize(mean_score),
        "min_health_score": round(min(scores), 1) if scores else 0.0,
        "max_health_score": round(max(scores), 1) if scores else 0.0,
        "dominant_problem_code": dominant,
        "dominant_problem_label": reporter.problem_label(dominant),
        "n_sections": n_sections,
    }

    return {
        "farm_id": farm_id,
        "farm_name": farm_name,
        "date": date,
        "field_summary": field_summary,
        "results": results,
    }
