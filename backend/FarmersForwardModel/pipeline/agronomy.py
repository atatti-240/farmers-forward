"""Deterministic agronomy logic -- the expert "oracle".

Two jobs:
  1. Generate ground-truth labels (health score, category, crop viability) for
     synthetic training data, so the ML models have real signal to learn.
  2. Provide reliable rule-based inference where precision matters more than a
     model's approximation (problem detection -> the recommender's fixes, and
     "what can I plant right now" crop viability).

Everything keys off pipeline.thresholds so the numbers stay in one place. Scoring
uses only the five measured properties: pH, EC (salinity, as ECe), and N / P / K.
Given a crop with a Texas A&M table (pipeline.crop_profile), pH, salinity and
N/P/K are judged against that crop; otherwise the generic Texas thresholds apply.
"""

from __future__ import annotations

from typing import Dict, List
import math

from . import crop_profile, thresholds as T


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


def _eval_ph(v: float, crop=None) -> List[Dict]:
    # No penalty inside the optimal band; it grows to 8 at the edge of the
    # tolerable band, then steeply beyond it. Generic band 6.0-7.0 (tolerable
    # 5.5-7.8); a crop with an A&M table uses its own.
    if crop is not None and crop.mapped:
        (opt_lo, opt_hi), (tol_lo, tol_hi) = crop.ph_band()
        for_crop = " for %s" % crop.label
        band = ", best %.1f-%.1f" % (opt_lo, opt_hi)
    else:
        opt_lo, opt_hi = T.PH["ideal_low"], T.PH["ideal_high"]
        tol_lo, tol_hi = T.PH["alert_low"], T.PH["alert_high"]
        for_crop = band = ""
    if opt_lo <= v <= opt_hi:
        return []
    if v < opt_lo:                     # acidic side
        if v >= tol_lo:                                  # optimal -> tolerable edge
            pen = _lin(v, opt_lo, tol_lo, 0, 8)
        elif v >= tol_lo - 1.0:                          # 1 unit past it
            pen = _lin(v, tol_lo, tol_lo - 1.0, 8, 28)
        else:
            pen = _lin(v, tol_lo - 1.0, tol_lo - 2.0, 28, 35)
        return [_problem("ph_acidic", "ph_acidic", "ph", v, pen,
                         "Soil is too acidic%s (pH %.1f%s); nutrients lock up and "
                         "aluminium toxicity rises." % (for_crop, v, band))]
    # alkaline side
    if v <= tol_hi:                                      # optimal -> tolerable edge
        pen = _lin(v, opt_hi, tol_hi, 0, 8)
    elif v <= tol_hi + 0.7:                              # 0.7 units past it
        pen = _lin(v, tol_hi, tol_hi + 0.7, 8, 22)
    else:
        pen = _lin(v, tol_hi + 0.7, tol_hi + 1.7, 22, 32)
    return [_problem("ph_alkaline", "ph_alkaline", "ph", v, pen,
                     "Soil is too alkaline%s (pH %.1f%s); iron, phosphorus and zinc "
                     "become less available." % (for_crop, v, band))]


def _ec_penalty(v: float) -> float:
    """Generic salinity penalty above the 2 dS/m stress line."""
    if v <= T.EC["severe"]:                              # 2 - 4
        return _lin(v, 2, 4, 3, 15)
    if v <= T.EC["lethal"]:                              # 4 - 8
        return _lin(v, 4, 8, 15, 32)
    return _lin(v, 8, 12, 32, 45)                        # > 8


def _eval_ec(v: float, crop=None) -> List[Dict]:
    if crop is not None and crop.mapped:
        sal = crop.salinity(v)
        if sal["status"] == "ok":
            return []
        thr, ry = sal["threshold"], sal["relative_yield_pct"]
        if ry is None:
            # Rating-only crops publish no yield slope: run the generic curve
            # from the crop's own threshold.
            pen = _ec_penalty(v - thr + T.EC["stress"])
            outlook = ""
        else:
            # Maas-Hoffman yield loss: 3 just past the threshold, then half a
            # point per percent of yield lost.
            pen = min(45.0, 3.0 + 0.5 * (100.0 - ry))
            outlook = "; expect ~%.0f%% of full yield" % ry
        return [_problem("salinity", "salinity", "ec", v, pen,
                         "Salinity is above the %s salt threshold (ECe %.1f dS/m, "
                         "threshold %.1f%s)." % (crop.label, v, thr, outlook))]
    if v <= T.EC["stress"]:
        return []
    return [_problem("salinity", "salinity", "ec", v, _ec_penalty(v),
                     "Salinity is high (ECe %.1f dS/m); salt stress reduces water "
                     "uptake and yield." % v)]


# Generic penalty at 0 ppm for each nutrient; A&M deficiencies scale up to it.
_NUTRIENT_MAX_PENALTY = {"N": 16.0, "P": 10.0, "K": 10.0}
_NUTRIENT_NAMES = {"N": "Nitrogen", "P": "Phosphorus", "K": "Potassium"}


def _eval_crop_nutrient(nutrient: str, metric: str, v: float, crop) -> List[Dict]:
    """P/K are deficient when the crop's A&M table says to add any fertilizer
    at this soil test. N is only when the rate is at least
    T.AM_N_PROBLEM_FRACTION of the 0-ppm rate, since A&M recommends N almost
    everywhere; smaller N rates are routine (see recommender). Penalty runs
    from 2 at the flagging point up to the generic maximum at 0 ppm."""
    add = crop.fert_rec(nutrient, v)
    if not add:
        return []
    share = add / crop.fert_rec(nutrient, 0.0)
    if nutrient == "N":
        if share < T.AM_N_PROBLEM_FRACTION:
            return []
        pen = _lin(share, T.AM_N_PROBLEM_FRACTION, 1, 2, _NUTRIENT_MAX_PENALTY["N"])
        message = ("Nitrogen is low for %s (%.0f ppm); expect pale, stunted "
                   "growth without fertilizer." % (crop.label, v))
    else:
        pen = _lin(share, 0, 1, 2, _NUTRIENT_MAX_PENALTY[nutrient])
        suff = crop.sufficiency(nutrient)
        message = ("%s is below the Texas A&M level for %s (%.0f ppm%s)."
                   % (_NUTRIENT_NAMES[nutrient], crop.label, v,
                      "; sufficient at %.0f ppm" % suff if suff is not None else ""))
    return [_problem(metric + "_deficient", metric + "_deficient", metric, v, pen, message)]


def _eval_nitrogen(v: float, crop=None) -> List[Dict]:
    if crop is not None and crop.has_table("N"):
        return _eval_crop_nutrient("N", "nitrogen", v, crop)
    if v >= T.NITROGEN["deficient"]:
        return []
    if v >= 10:
        pen = _lin(v, 20, 10, 2, 10)
    else:
        pen = _lin(v, 10, 0, 10, 16)
    return [_problem("nitrogen_deficient", "nitrogen_deficient", "nitrogen", v, pen,
                     "Nitrogen is deficient (%.0f ppm); expect pale, stunted "
                     "growth." % v)]


def _eval_phosphorus(v: float, crop=None) -> List[Dict]:
    if v > T.PHOSPHORUS["excess"]:                      # > 150, any crop
        pen = _lin(v, 150, 300, 3, 12)
        return [_problem("phosphorus_excess", "phosphorus_excess", "phosphorus", v, pen,
                         "Phosphorus is very high (%.0f ppm); runoff and "
                         "water-quality risk, wasted inputs." % v)]
    if crop is not None and crop.has_table("P"):
        return _eval_crop_nutrient("P", "phosphorus", v, crop)
    if v < T.PHOSPHORUS["deficient"]:                   # < 15
        pen = _lin(v, 15, 0, 2, 10)
        return [_problem("phosphorus_deficient", "phosphorus_deficient", "phosphorus", v, pen,
                         "Phosphorus is deficient (%.0f ppm); poor root and "
                         "early growth." % v)]
    return []


def _eval_potassium(v: float, crop=None) -> List[Dict]:
    if crop is not None and crop.has_table("K"):
        return _eval_crop_nutrient("K", "potassium", v, crop)
    thr = T.POTASSIUM["deficient"]
    if v >= thr:
        return []
    pen = _lin(v, thr, 0, 2, 10)
    return [_problem("potassium_deficient", "potassium_deficient", "potassium", v, pen,
                     "Potassium is deficient (%.0f ppm, threshold %.0f ppm)."
                     % (v, thr))]


def detect_problems(sample: Dict, crop=None) -> List[Dict]:
    """Return every threshold violation as a problem dict, sorted worst-first.

    `crop` (a form crop name or crop_profile.Crop) judges pH, salinity and N/P/K
    against that crop's Texas A&M numbers. Without one, or for a crop with no
    A&M table, the generic thresholds apply (as they do for ML training labels).
    """
    s = sample
    crop = crop_profile.as_crop(crop)
    problems: List[Dict] = []
    problems += _eval_ph(float(s["ph"]), crop)
    problems += _eval_ec(float(s["ec"]), crop)
    problems += _eval_nitrogen(float(s["nitrogen"]), crop)
    problems += _eval_phosphorus(float(s["phosphorus"]), crop)
    problems += _eval_potassium(float(s["potassium"]), crop)
    # keep only meaningful ones, worst first
    problems = [p for p in problems if p["penalty"] >= 1.0]
    problems.sort(key=lambda p: p["penalty"], reverse=True)
    return problems


def compute_health_score(sample: Dict, crop=None) -> float:
    """Deterministic 0-100 soil health score (clamped).

    Starts at 100 and subtracts every problem's penalty across the five measured
    properties (pH distance from the optimum band, EC salinity, and N/P/K
    deficiencies/excesses vs thresholds) -- the crop's, if given; with a crop,
    pH outside its tolerable band caps the score at the top of Fair. Stacked severe
    problems can drive the raw total well below zero -- the clamp is intentional
    and is also enforced again on the ML regressor's output at inference time.

    Consequence for the label contract (no crop): a soil with pH 6.0-7.0 AND
    EC < 2.0 AND N > 20 AND P > 15 AND K > 100 incurs zero penalty and scores
    100 (>= 80, "Excellent").
    """
    crop = crop_profile.as_crop(crop)
    total_penalty = sum(p["penalty"] for p in detect_problems(sample, crop))
    score = max(0.0, min(100.0, 100.0 - total_penalty))
    if crop is not None and crop.mapped:
        _, (tol_lo, tol_hi) = crop.ph_band()
        if not tol_lo <= float(sample["ph"]) <= tol_hi:
            score = min(score, T.PH_OUT_OF_TOLERANCE_SCORE_CAP)  # Fair at best
    return score


# Backwards/forwards-compatible alias: some callers refer to the health-score
# function as `soil_health_score`.
soil_health_score = compute_health_score


def primary_problem(sample: Dict, crop=None) -> Dict:
    """The single most severe problem, or a 'healthy' sentinel."""
    problems = detect_problems(sample, crop)
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
def crop_fit(sample: Dict, crop) -> Dict:
    """How current conditions suit a crop with an A&M table.

    status is "ideal" (pH in the optimal band, no salt yield loss),
    "viable_with_adjustments" (pH within the tolerable band and salinity
    relative yield >= T.VIABLE_MIN_RELATIVE_YIELD), or "not_viable".
    limiting_factors explain a not-viable status; adjustments are the softer
    issues of a viable crop. Nutrient shortfalls are neither: they become
    fertilizer recommendations. This one test backs both the selected-crop
    assessment and the "viable now" list, so the two cannot disagree.
    """
    crop = crop_profile.as_crop(crop)
    ph, ece = float(sample["ph"]), float(sample["ec"])
    (opt_lo, opt_hi), (tol_lo, tol_hi) = crop.ph_band()
    estimated = " (estimated range)" if crop.profile()["ph_confidence"] != "standard" else ""
    limits: List[str] = []
    adjustments: List[str] = []
    if not tol_lo <= ph <= tol_hi:
        limits.append("pH %.1f is outside the %.1f-%.1f tolerable range for %s%s; "
                      "expect yield loss or nutrient lockup"
                      % (ph, tol_lo, tol_hi, crop.label, estimated))
    elif not opt_lo <= ph <= opt_hi:
        adjustments.append("pH %.1f is %s the %.1f-%.1f optimum for %s%s" % (
            ph, "below" if ph < opt_lo else "above", opt_lo, opt_hi, crop.label, estimated))
    sal = crop.salinity(ece)
    if sal["status"] != "ok":
        ry = sal["relative_yield_pct"]
        text = "ECe %.1f dS/m is above the %.1f dS/m salt threshold for %s%s; %s" % (
            ece, sal["threshold"], crop.label,
            " (threshold estimated from a similar crop)" if sal["data_source"] == "EST" else "",
            "expect ~%.0f%% of full yield" % ry if ry is not None
            else "yield loss unknown (no published slope)")
        # Unknown yield loss cannot show the minimum relative yield is met.
        if ry is None or ry < T.VIABLE_MIN_RELATIVE_YIELD:
            limits.append(text)
        else:
            adjustments.append(text)
    status = ("not_viable" if limits else
              "viable_with_adjustments" if adjustments else "ideal")
    return {"status": status, "limiting_factors": limits, "adjustments": adjustments}


def crop_viability(sample: Dict) -> Dict:
    """Which A&M-mapped crops suit current conditions (crop_fit).

    Returns dict(viable=[names], ideal=[names], with_adjustments=[names],
    not_viable=[{crop, limiting_factors:[...]}]); viable = ideal + with_adjustments.
    """
    out: Dict = {"viable": [], "ideal": [], "with_adjustments": [], "not_viable": []}
    for crop in T.CROP_DEFAULT_YIELD:
        fit = crop_fit(sample, crop)
        if fit["status"] == "not_viable":
            out["not_viable"].append({"crop": crop, "limiting_factors": fit["limiting_factors"]})
            continue
        out["viable"].append(crop)
        out["ideal" if fit["status"] == "ideal" else "with_adjustments"].append(crop)
    return out


def farmersfirst_crop_viability(sample: Dict) -> Dict:
    """Viability under the FarmersFirst T.CROPS rules, which label the ML crop
    classifier's training data (and back crops with no A&M table, like hemp).

    Same shape as crop_viability. A crop is viable only if pH is in range AND
    EC <= max AND N/P/K >= minimums.
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
    viable = set(farmersfirst_crop_viability(sample)["viable"])
    return [1 if c in viable else 0 for c in T.CROP_NAMES]
