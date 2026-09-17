"""Deterministic agronomy logic -- the expert "oracle".

Two jobs:
  1. Generate ground-truth labels (health score, category, crop viability) for
     synthetic training data, so the ML models have real signal to learn.
  2. Provide reliable rule-based inference where precision matters more than a
     model's approximation (problem detection -> the recommender's fixes, and
     "what can I plant right now" crop viability).

Everything keys off pipeline.thresholds so the numbers stay in one place. Scoring
uses only the five measured properties: pH, EC (salinity), and N / P / K.
"""

from __future__ import annotations

from typing import Dict, List
import math

from . import thresholds as T


def nitrogen_credit_from_organic_matter(
        om_pct: float, *, n_ppm_per_om_pct: float = T.N_CREDIT_PPM_PER_OM_PCT) -> float:
    """Estimate the user-specified mineralized-N credit: OM% * 15 ppm.

    The default approximates 3% annual mineralization, or OM% * 30 lb N/acre/year,
    converted using 2 lb/acre per ppm. OM is supplied in percentage points:
    2% OM yields 30 ppm. This is a derived annual N-credit proxy, not measured
    nitrate-N or total nitrogen. An explicit coefficient can override the default.
    """
    if not math.isfinite(om_pct) or not 0 <= om_pct <= 100:
        raise ValueError("Organic matter must be finite and between 0 and 100 percent.")
    if not math.isfinite(n_ppm_per_om_pct) or n_ppm_per_om_pct <= 0:
        raise ValueError("N-credit coefficient must be finite and positive.")
    return om_pct * n_ppm_per_om_pct


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _lin(x: float, x0: float, x1: float, p0: float, p1: float) -> float:
    """Linear interpolation of penalty from p0 (at x0) to p1 (at x1), clamped
    to the [p0, p1] band. Used to grow a penalty smoothly across a threshold."""
    if x1 == x0:
        return p1
    frac = (x - x0) / (x1 - x0)
    frac = max(0.0, min(1.0, frac))
    return p0 + frac * (p1 - p0)


def _sev_label(penalty: float) -> str:
    if penalty >= 15:
        return "severe"
    if penalty >= 7:
        return "moderate"
    return "mild"


# ---------------------------------------------------------------------------
# per-metric problem evaluation. Each returns a problem dict or None.
# `penalty` doubles as the severity weight: the biggest penalty is the primary
# problem, and health_score = 100 - sum(penalties).
# `fix_code` links the problem to a recommender rule.
# ---------------------------------------------------------------------------
def _problem(code, fix_code, metric, value, penalty, message):
    return {
        "code": code,
        "fix_code": fix_code,
        "metric": metric,
        "value": round(float(value), 3),
        "penalty": round(float(penalty), 2),
        "severity": _sev_label(penalty),
        "message": message,
    }


def _eval_ph(v: float) -> List[Dict]:
    # Optimal band is 6.0-7.0; penalty grows with distance outside it.
    if T.PH["ideal_low"] <= v <= T.PH["ideal_high"]:
        return []
    if v < T.PH["ideal_low"]:          # acidic side
        if v >= T.PH["alert_low"]:                       # 5.5 - 6.0 mild
            pen = _lin(v, 6.0, 5.5, 0, 8)
        elif v >= 4.5:                                   # 4.5 - 5.5
            pen = _lin(v, 5.5, 4.5, 8, 28)
        else:                                            # < 4.5
            pen = _lin(v, 4.5, 3.5, 28, 35)
        return [_problem("ph_acidic", "ph_acidic", "ph", v, pen,
                         "Soil is too acidic (pH %.1f); nutrients lock up and "
                         "aluminium toxicity rises." % v)]
    # alkaline side (> 7.0)
    if v <= T.PH["alert_high"]:                          # 7.0 - 7.8 mild-rising
        pen = _lin(v, 7.0, 7.8, 0, 8)
    elif v <= 8.5:                                       # 7.8 - 8.5
        pen = _lin(v, 7.8, 8.5, 8, 22)
    else:                                                # > 8.5
        pen = _lin(v, 8.5, 9.5, 22, 32)
    return [_problem("ph_alkaline", "ph_alkaline", "ph", v, pen,
                     "Soil is too alkaline (pH %.1f); iron, phosphorus and zinc "
                     "become less available." % v)]


def _eval_ec(v: float) -> List[Dict]:
    if v <= T.EC["stress"]:
        return []
    if v <= T.EC["severe"]:                              # 2 - 4
        pen = _lin(v, 2, 4, 3, 15)
    elif v <= T.EC["lethal"]:                            # 4 - 8
        pen = _lin(v, 4, 8, 15, 32)
    else:                                                # > 8
        pen = _lin(v, 8, 12, 32, 45)
    return [_problem("salinity", "salinity", "ec", v, pen,
                     "Salinity is high (EC %.1f dS/m); salt stress reduces water "
                     "uptake and yield." % v)]


def _eval_nitrogen(v: float) -> List[Dict]:
    if v >= T.NITROGEN["deficient"]:
        return []
    if v >= 10:
        pen = _lin(v, 20, 10, 2, 10)
    else:
        pen = _lin(v, 10, 0, 10, 16)
    return [_problem("nitrogen_deficient", "nitrogen_deficient", "nitrogen", v, pen,
                     "Nitrogen is deficient (%.0f ppm); expect pale, stunted "
                     "growth." % v)]


def _eval_phosphorus(v: float) -> List[Dict]:
    if v < T.PHOSPHORUS["deficient"]:                   # < 15
        pen = _lin(v, 15, 0, 2, 10)
        return [_problem("phosphorus_deficient", "phosphorus_deficient", "phosphorus", v, pen,
                         "Phosphorus is deficient (%.0f ppm); poor root and "
                         "early growth." % v)]
    if v > T.PHOSPHORUS["excess"]:                      # > 150
        pen = _lin(v, 150, 300, 3, 12)
        return [_problem("phosphorus_excess", "phosphorus_excess", "phosphorus", v, pen,
                         "Phosphorus is very high (%.0f ppm); runoff and "
                         "water-quality risk, wasted inputs." % v)]
    return []


def _eval_potassium(v: float) -> List[Dict]:
    thr = T.POTASSIUM["deficient"]
    if v >= thr:
        return []
    pen = _lin(v, thr, 0, 2, 10)
    return [_problem("potassium_deficient", "potassium_deficient", "potassium", v, pen,
                     "Potassium is deficient (%.0f ppm, threshold %.0f ppm)."
                     % (v, thr))]


def detect_problems(sample: Dict) -> List[Dict]:
    """Return every threshold violation as a problem dict, sorted worst-first."""
    s = sample
    problems: List[Dict] = []
    problems += _eval_ph(float(s["ph"]))
    problems += _eval_ec(float(s["ec"]))
    problems += _eval_nitrogen(float(s["nitrogen"]))
    problems += _eval_phosphorus(float(s["phosphorus"]))
    problems += _eval_potassium(float(s["potassium"]))
    # keep only meaningful ones, worst first
    problems = [p for p in problems if p["penalty"] >= 1.0]
    problems.sort(key=lambda p: p["penalty"], reverse=True)
    return problems


def compute_health_score(sample: Dict) -> float:
    """Deterministic 0-100 soil health score (clamped).

    Starts at 100 and subtracts every problem's penalty across the five measured
    properties (pH distance from the 6.0-7.0 optimum, EC salinity, and N/P/K
    deficiencies/excesses vs thresholds). Stacked severe problems can drive the
    raw total well below zero -- the clamp is intentional and is also enforced
    again on the ML regressor's output at inference time.

    Consequence for the label contract: a soil with pH 6.0-7.0 AND EC < 2.0 AND
    N > 20 AND P > 15 AND K > 100 incurs zero penalty and scores 100 (>= 80,
    "Excellent").
    """
    total_penalty = sum(p["penalty"] for p in detect_problems(sample))
    return max(0.0, min(100.0, 100.0 - total_penalty))


# Backwards/forwards-compatible alias: some callers refer to the health-score
# function as `soil_health_score`.
soil_health_score = compute_health_score


def primary_problem(sample: Dict) -> Dict:
    """The single most severe problem, or a 'healthy' sentinel."""
    problems = detect_problems(sample)
    if not problems:
        return {
            "code": "none",
            "fix_code": None,
            "metric": None,
            "value": None,
            "penalty": 0.0,
            "severity": "none",
            "message": "No threshold violations detected -- soil is in good shape.",
        }
    return problems[0]


# ---------------------------------------------------------------------------
# crop viability
# ---------------------------------------------------------------------------
def crop_viability(sample: Dict) -> Dict:
    """Which crops are viable *right now* given current conditions.

    Returns dict(viable=[names], not_viable=[{crop, limiting_factors:[...]}]).
    A crop is viable only if pH is in range AND EC <= max AND N/P/K >= minimums.
    """
    ph = float(sample["ph"])
    ec = float(sample["ec"])
    n = float(sample["nitrogen"])
    p = float(sample["phosphorus"])
    k = float(sample["potassium"])

    viable: List[str] = []
    not_viable: List[Dict] = []
    for crop, req in T.CROPS.items():
        reasons = []
        if ph < req["ph_min"]:
            reasons.append("pH %.1f below %.1f (too acidic)" % (ph, req["ph_min"]))
        elif ph > req["ph_max"]:
            reasons.append("pH %.1f above %.1f (too alkaline)" % (ph, req["ph_max"]))
        if ec > req["ec_max"]:
            reasons.append("EC %.1f above %.1f dS/m (too salty)" % (ec, req["ec_max"]))
        if n < req["n_min"]:
            reasons.append("N %.0f below %.0f ppm" % (n, req["n_min"]))
        if p < req["p_min"]:
            reasons.append("P %.0f below %.0f ppm" % (p, req["p_min"]))
        if k < req["k_min"]:
            reasons.append("K %.0f below %.0f ppm" % (k, req["k_min"]))
        if reasons:
            not_viable.append({"crop": crop, "limiting_factors": reasons})
        else:
            viable.append(crop)
    return {"viable": viable, "not_viable": not_viable}


def crop_viability_vector(sample: Dict) -> List[int]:
    """Binary viability per crop, in CROP_NAMES order. Used as ML multi-label y."""
    viable = set(crop_viability(sample)["viable"])
    return [1 if c in viable else 0 for c in T.CROP_NAMES]
