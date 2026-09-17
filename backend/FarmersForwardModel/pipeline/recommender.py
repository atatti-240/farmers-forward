"""Rule-based recommender: turns a detected problem into a specific, costed fix.

The whole point of FarmersFirst is actionability. Every recommendation carries a
*rate*, a *cost estimate*, and a *timing window* -- never just "your pH is low".

Fixes are keyed by the `fix_code` that pipeline.agronomy attaches to each
problem, so detection and remediation stay in lock-step. Rates scale with
severity. The five fixable properties are pH (acidic/alkaline), EC (salinity),
and N / P / K.

The cost/benefit estimator at the bottom is EXPERIMENTAL and clearly labelled as
such -- a ballpark, not financial advice.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from . import agronomy, thresholds as T


def _money(low: float, high: float, unit: str = "/acre") -> str:
    def one(v: float) -> str:
        return ("-${:,.0f}".format(abs(v))) if v < 0 else ("${:,.0f}".format(v))
    return "~{} to {}{}".format(one(low), one(high), unit)


# Crop-specific nitrogen demand: the target available-N proxy (ppm) each crop is
# fed toward when sizing an N correction. Legumes (soybeans) sit low because they
# fix much of their own N; heavy feeders (corn, tomatoes, hemp) sit high. Used by
# _fix_nitrogen so the recommended rate reflects the crop actually being grown
# (Bug 3 fix from the v2 audit: the demand table was previously ignored).
CROP_N_DEMAND = {
    "Corn": 50.0, "Cotton": 35.0, "Sorghum": 35.0, "Soybeans": 25.0,
    "Wheat": 45.0, "Tomatoes": 50.0, "Peppers": 45.0, "Blueberries": 25.0,
    "Strawberries": 35.0, "Hemp": 50.0,
}
DEFAULT_N_TARGET = 40.0  # generic target when no crop is specified


# ---------------------------------------------------------------------------
# One builder per fix_code. Each takes the raw sample + the problem dict and
# returns a fully specified recommendation. Rates are Texas-oriented.
# ---------------------------------------------------------------------------
def _fix_ph_acidic(s: Dict, p: Dict) -> Dict:
    ph = float(s["ph"])
    # tons/acre of ag lime: the more acidic, the more lime.
    tons = max(1.0, min(4.5, agronomy._lin(ph, 6.0, 4.5, 1.0, 4.0)))
    low, high = tons * 25.0, tons * 40.0
    return dict(
        title="Raise pH with agricultural lime",
        action="Apply agricultural lime (calcitic; use dolomitic if Mg is also "
               "low) and incorporate 6 in deep.",
        rate="{:.1f}-{:.1f} tons/acre".format(tons, tons + 0.5),
        cost_estimate=_money(low, high) + " ($25-40/ton material)",
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="3-6 months before planting (fall for a spring crop) so it has "
               "time to react.",
        notes="Re-test pH after one season; lime acts slowly. Sandy East-TX "
              "soils need less than Blackland clay.",
    )


def _fix_ph_alkaline(s: Dict, p: Dict) -> Dict:
    ph = float(s["ph"])
    # elemental sulfur, lb/acre, to nudge pH down ~0.5-1.0 on a non-calcareous soil
    lb = agronomy._lin(ph, 7.8, 8.6, 300.0, 1000.0)
    low, high = lb * 0.30, lb * 0.55
    return dict(
        title="Lower pH / manage alkalinity",
        action="Apply elemental sulfur and use acidifying fertilizers "
               "(ammonium sulfate). On calcareous Blackland clay, fully "
               "lowering pH is slow and costly -- lean on tolerant crops + "
               "chelated micronutrients instead.",
        rate="{:.0f}-{:.0f} lb/acre elemental sulfur".format(lb, lb + 200),
        cost_estimate=_money(low, high) + " (sulfur ~$0.30-0.55/lb)",
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="Apply and incorporate several months ahead; oxidation to acid "
               "takes weeks-months and warm soil.",
        notes="Test for free lime first. If the soil fizzes with acid it is "
              "calcareous and sulfur demand is very high -- prefer cotton, "
              "sorghum and other alkaline-tolerant crops.",
    )


def _fix_salinity(s: Dict, p: Dict) -> Dict:
    ec = float(s["ec"])
    lines = ["Leach salts with good-quality water once drainage is adequate "
             "(apply ~6 in extra water to push salts below the root zone)."]
    low, high = 20.0, 60.0   # leaching / water-management labour & water
    if ec > T.EC["lethal"]:
        lines.append("At this salinity, install tile/subsurface drainage and "
                     "reclaim over a season before expecting a crop.")
        low += 40; high += 120
    return dict(
        title="Reduce salinity (leach + drain)",
        action=" ".join(lines),
        rate="Leaching fraction sized to soil; add drainage if water ponds.",
        cost_estimate=_money(low, high),
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="Pre-season, when you can move water through the profile.",
        notes="Test your IRRIGATION water too -- Brazos water in the Fort Bend/"
              "Richmond reach runs saltier downstream, so over-irrigating with "
              "it adds salt. Meanwhile grow salt-tolerant cotton or sorghum.",
    )


def _fix_nitrogen(s: Dict, p: Dict, target_crop: Optional[str]) -> Dict:
    n = float(s["nitrogen"])
    # Bring soil N toward the target for the crop being grown (or a generic
    # target when none is specified), then convert to lb N/acre and urea.
    target = CROP_N_DEMAND.get(target_crop, DEFAULT_N_TARGET)
    lb_n = max(30.0, min(160.0, (target - n) * 4.0 + 30.0))
    urea_lb = lb_n / 0.46
    low, high = lb_n * 0.55, lb_n * 0.75
    if target_crop in CROP_N_DEMAND:
        crop_note = ("Rate sized for %s using the existing crop profile "
                     "(target ~%.0f ppm soil N). " % (target_crop, target))
    elif target_crop:
        crop_note = ("No nitrogen profile is available for %s. This rate uses "
                     "the generic ~%.0f ppm soil-N target; confirm a crop-specific "
                     "rate with your soil lab. " % (target_crop, target))
    else:
        crop_note = ("Rate sized to a generic ~%.0f ppm soil-N target; specify a "
                     "crop for a tailored rate. " % target)
    return dict(
        title="Correct nitrogen deficiency",
        action="Apply nitrogen fertilizer (urea 46-0-0 or UAN). Split the "
               "application on corn/leafy crops to limit leaching.",
        rate="{:.0f}-{:.0f} lb N/acre (~{:.0f} lb urea)".format(lb_n, lb_n + 20, urea_lb),
        cost_estimate=_money(low, high) + " (N ~$0.55-0.75/lb)",
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="At/near planting; split-apply for corn (pre-plant + side-dress).",
        notes=crop_note + "A legume cover crop can fix 40-100 lb N/acre "
              "biologically and cut this bill over time.",
    )


def _fix_phosphorus_def(s: Dict, p: Dict) -> Dict:
    p_ppm = float(s["phosphorus"])
    p2o5 = max(30.0, min(70.0, (20.0 - p_ppm) * 3.0 + 30.0))
    low, high = p2o5 * 0.60, p2o5 * 0.90
    return dict(
        title="Correct phosphorus deficiency",
        action="Band phosphate (DAP 18-46-0 or triple superphosphate) near the "
               "seed row for best early uptake.",
        rate="{:.0f}-{:.0f} lb P2O5/acre".format(p2o5, p2o5 + 15),
        cost_estimate=_money(low, high) + " (P2O5 ~$0.60-0.90/lb)",
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="At planting (banded/starter).",
        notes="Banding beats broadcasting on high-pH soils where P gets tied up.",
    )


def _fix_phosphorus_excess(s: Dict, p: Dict) -> Dict:
    return dict(
        title="Stop over-applying phosphorus",
        action="Halt all P fertilizer and manure. Protect water: buffer strips, "
               "cover crops, and erosion control to stop P-laden runoff.",
        rate="0 lb P2O5/acre until soil test P drops below 150 ppm.",
        cost_estimate="$0 (a saving) + buffer/cover-crop cost",
        cost_per_acre_low=0.0, cost_per_acre_high=0.0,
        timing="Immediately, and ongoing.",
        notes="High P is a runoff / water-quality liability, not a yield gain. "
              "Draw it down through crop removal over several seasons.",
    )


def _fix_potassium(s: Dict, p: Dict) -> Dict:
    k = float(s["potassium"])
    thr = T.POTASSIUM["deficient"]
    k2o = max(40.0, min(120.0, (thr - k) * 1.2 + 40.0))
    low, high = k2o * 0.45, k2o * 0.70
    return dict(
        title="Correct potassium deficiency",
        action="Apply potash (muriate of potash 0-0-60). Split applications on "
               "sandy or leaching-prone ground.",
        rate="{:.0f}-{:.0f} lb K2O/acre".format(k2o, k2o + 20),
        cost_estimate=_money(low, high) + " (K2O ~$0.45-0.70/lb)",
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="Pre-plant or at planting.",
        notes="K deficiency threshold is {:.0f} ppm. Sandy ground holds less K "
              "than clay, so the sandier the field the more frequent and smaller "
              "the doses should be.".format(thr),
    )


def normalize_crop_name(crop: Optional[str]) -> Optional[str]:
    """Match known profiles without changing the stored custom crop label."""
    if not isinstance(crop, str) or not crop.strip():
        return None
    name = " ".join(crop.split())
    aliases = {"maize": "Corn", "soybean": "Soybeans", "soya": "Soybeans",
               "tomato": "Tomatoes", "pepper": "Peppers",
               "blueberry": "Blueberries", "strawberry": "Strawberries"}
    known = {key.casefold(): key for key in T.CROPS}
    return known.get(name.casefold(), aliases.get(name.casefold(), name))


def assess_target_crop(sample: Dict, target_crop: Optional[str]) -> Dict:
    """Report limits for the selected crop using only existing crop profiles.

    A custom label does not imply known thresholds or a new ML crop class.
    """
    crop = normalize_crop_name(target_crop)
    if crop not in T.CROPS:
        return {
            "profile_name": None, "profile_available": False,
            "within_profile": None, "limiting_factors": [],
            "note": ("No crop-specific profile is available for %s. Recommendations "
                     "use general soil guidance; confirm crop requirements with "
                     "your soil lab." % crop) if crop else "Crop was not recorded for this sample.",
        }
    viability = agronomy.crop_viability(sample)
    limits = next((item["limiting_factors"] for item in viability["not_viable"]
                   if item["crop"] == crop), [])
    return {
        "profile_name": crop, "profile_available": True,
        "within_profile": not limits, "limiting_factors": limits,
        "note": (("Readings meet the existing %s soil profile." % crop) if not limits
                 else "Review these soil limits for %s before planting or applying amendments." % crop),
    }


def build_recommendations(sample: Dict,
                          problems: Optional[List[Dict]] = None,
                          target_crop: Optional[str] = None) -> List[Dict]:
    """Map detected problems to concrete fixes, worst-first, de-duplicated.

    Every returned dict carries rate/cost/timing.
    """
    target_crop = normalize_crop_name(target_crop)
    if problems is None:
        problems = agronomy.detect_problems(sample)

    recs: List[Dict] = []
    seen_fix_codes = set()
    for prob in problems:
        fc = prob.get("fix_code")
        if fc is None or fc in seen_fix_codes:
            continue
        seen_fix_codes.add(fc)

        if fc == "ph_acidic":
            rec = _fix_ph_acidic(sample, prob)
        elif fc == "ph_alkaline":
            rec = _fix_ph_alkaline(sample, prob)
        elif fc == "salinity":
            rec = _fix_salinity(sample, prob)
        elif fc == "nitrogen_deficient":
            rec = _fix_nitrogen(sample, prob, target_crop)
        elif fc == "phosphorus_deficient":
            rec = _fix_phosphorus_def(sample, prob)
        elif fc == "phosphorus_excess":
            rec = _fix_phosphorus_excess(sample, prob)
        elif fc == "potassium_deficient":
            rec = _fix_potassium(sample, prob)
        else:
            continue

        rec.update({
            "problem_code": prob["code"],
            "fix_code": fc,
            "severity": prob["severity"],
            "priority_weight": prob["penalty"],
            "addresses": prob["message"],
        })
        recs.append(rec)

    recs.sort(key=lambda r: r["priority_weight"], reverse=True)
    return recs


# ---------------------------------------------------------------------------
# EXPERIMENTAL cost / benefit -- ballpark only, NOT financial advice
# ---------------------------------------------------------------------------
COST_BENEFIT_DISCLAIMER = (
    "EXPERIMENTAL ESTIMATE -- NOT FINANCIAL ADVICE. These are rough, "
    "order-of-magnitude ballparks to help prioritize, using generic Texas input "
    "prices and assumed yield responses. Real costs and returns vary widely with "
    "crop, market, weather and your operation. Confirm with your county "
    "Extension agent and your own numbers before spending money."
)

# Rough share of a crop's gross revenue put at risk by each unaddressed problem,
# by severity. Deliberately conservative and wide.
_RISK_BY_SEVERITY = {"severe": (0.20, 0.45), "moderate": (0.08, 0.22), "mild": (0.02, 0.08)}

# Reference gross revenue per acre for a typical Texas row crop, used only to
# turn a "% of revenue protected" into dollars for the ballpark.
_REFERENCE_REVENUE_PER_ACRE = (500.0, 1100.0)


def estimate_cost_benefit(sample: Dict,
                          recommendations: List[Dict],
                          reference_revenue_per_acre=None) -> Dict:
    """Very rough cost-vs-benefit ballpark for implementing the fixes."""
    lo_rev, hi_rev = reference_revenue_per_acre or _REFERENCE_REVENUE_PER_ACRE

    items = []
    total_cost_low = total_cost_high = 0.0
    total_benefit_low = total_benefit_high = 0.0
    for rec in recommendations:
        c_low = float(rec.get("cost_per_acre_low", 0.0))
        c_high = float(rec.get("cost_per_acre_high", 0.0))
        r_low, r_high = _RISK_BY_SEVERITY.get(rec.get("severity", "mild"), (0.02, 0.08))
        b_low = r_low * lo_rev
        b_high = r_high * hi_rev
        total_cost_low += c_low
        total_cost_high += c_high
        total_benefit_low += b_low
        total_benefit_high += b_high
        items.append({
            "fix": rec["title"],
            "severity": rec.get("severity"),
            "est_cost_per_acre": _money(c_low, c_high),
            "est_value_protected_per_acre": _money(b_low, b_high, "/acre/yr"),
            "note": "Benefit = assumed {:.0f}-{:.0f}% of a ${:,.0f}-{:,.0f}/acre "
                    "reference crop protected by fixing a {} problem."
                    .format(r_low * 100, r_high * 100, lo_rev, hi_rev,
                            rec.get("severity", "mild")),
        })

    return {
        "experimental": True,
        "disclaimer": COST_BENEFIT_DISCLAIMER,
        "reference_revenue_per_acre": _money(lo_rev, hi_rev),
        "items": items,
        "total_est_cost_per_acre": _money(total_cost_low, total_cost_high),
        "total_est_value_protected_per_acre": _money(total_benefit_low, total_benefit_high, "/acre/yr"),
        "rough_first_year_net_per_acre": _money(
            total_benefit_low - total_cost_high,
            total_benefit_high - total_cost_low, "/acre/yr"),
    }
