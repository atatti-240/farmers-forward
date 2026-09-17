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
    """Extract the five soil measurements as plain floats."""
    _ensure_pipeline_path()
    from pipeline import thresholds as T  # light (plain dicts)
    return {c: float(section[c]) for c in T.MEASUREMENT_COLUMNS}


def run_analysis(farm_name: str, date: str, sections: List[Dict],
                 farm_id: Optional[int] = None) -> Dict:
    """Analyze every section and return the full field report.

    `sections` is a list of dicts with keys: section_label, latitude, longitude,
    crop_type, ph, ec, nitrogen, phosphorus, potassium. Returns the structure defined in
    BUILD_CONTRACT §2.5.
    """
    # Lazy heavy imports (see module note): only paid on the first submission.
    _ensure_pipeline_path()
    import pandas as pd
    from pipeline import agronomy
    from pipeline import model as M
    from pipeline import preprocess
    from pipeline import recommender
    from pipeline import reporter
    from pipeline import thresholds as T

    bundle = _get_bundle()
    prefix = farm_id if farm_id is not None else "F"

    # Build the feature/id frame the pipeline expects.
    rows = []
    for sec in sections:
        label = sec["section_label"]
        m = _measurements(sec)
        rows.append({
            "sample_id": "%s-%s" % (prefix, label),
            "latitude": float(sec["latitude"]),
            "longitude": float(sec["longitude"]),
            "region": "fort_bend",
            "zone": label,
            "ph": m["ph"],
            "ec": m["ec"],
            "nitrogen": m["nitrogen"],
            "phosphorus": m["phosphorus"],
            "potassium": m["potassium"],
        })
    df = pd.DataFrame(rows)

    features = preprocess.build_features(df)
    ml_results = M.predict_frame(bundle, features)

    results: List[Dict] = []
    scores: List[float] = []
    problem_codes: List[str] = []

    for sec, ml in zip(sections, ml_results):
        label = sec["section_label"]
        m = _measurements(sec)

        problems = agronomy.detect_problems(m)
        primary = agronomy.primary_problem(m)
        rule_score = agronomy.compute_health_score(m)
        viability = agronomy.crop_viability(m)
        crop_type = sec.get("crop_type")
        recs = recommender.build_recommendations(m, problems, target_crop=crop_type)
        selected_crop = recommender.assess_target_crop(m, crop_type)
        cost_benefit = recommender.estimate_cost_benefit(m, recs)

        health_score = round(float(ml["ml_health_score"]), 1)
        health_category = ml["ml_category_from_score"]
        scores.append(health_score)
        if primary["code"] != "none":
            problem_codes.append(primary["code"])

        results.append({
            "section_label": label,
            "crop_type": crop_type,
            "selected_crop": selected_crop,
            "sample_id": "%s-%s" % (prefix, label),
            "latitude": float(sec["latitude"]),
            "longitude": float(sec["longitude"]),
            "inputs": {c: round(m[c], 2) for c in T.MEASUREMENT_COLUMNS},
            "health_score": health_score,
            "health_category": health_category,
            "rule_health_score": round(float(rule_score), 1),
            "color": score_color(health_score),
            "primary_problem": {
                "code": primary["code"],
                "label": reporter.problem_label(primary["code"]),
                "severity": primary["severity"],
                "message": primary["message"],
            },
            "problems": problems,
            "recommendations": recs,
            "crop_suitability": {
                "viable_now": viability["viable"],
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
