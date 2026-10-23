"""Rule-based recommender: turns a detected problem into a specific fix.

The whole point of FarmersFirst is actionability. Every recommendation says what
to do, how much (`rate`, shown as the amount) and when -- never just "your pH is
low".

Fixes are keyed by the `fix_code` that pipeline.agronomy attaches to each
problem, so detection and remediation stay in lock-step. The five fixable
properties are pH (acidic/alkaline), EC (salinity), and N / P / K. Reports only
show Texas A&M numbers: N/P/K amounts live in the report's Fertilizer Plan
(backend/fert_calc.py, from crop_lookup.fert_rec() for the crop and yield
goal), so an N/P/K item points there, and pH items refer the farmer to the A&M
lab for a lime or sulfur rate instead of estimating one.

The cost/benefit estimator at the bottom is EXPERIMENTAL and clearly labelled as
such -- a ballpark, not financial advice, and not shown on reports.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from . import agronomy, crop_profile, thresholds as T
from .crop_profile import normalize_crop_name  # noqa: F401 (public API)


def _money(low: float, high: float, unit: str = "/acre") -> str:
    def one(v: float) -> str:
        return ("-${:,.0f}".format(abs(v))) if v < 0 else ("${:,.0f}".format(v))
    return "~{} to {}{}".format(one(low), one(high), unit)


DEFAULT_N_TARGET = 40.0  # generic soil-N target for crops without an A&M N table


def _am_rate(crop, nutrient: str, ppm: float) -> Optional[float]:
    """The crop's Texas A&M rate at this soil test, or None without a table."""
    if crop is None or not crop.has_table(nutrient):
        return None
    return crop.fert_rec(nutrient, ppm)


def _am_rate_note(crop, nutrient: str, ppm: float) -> str:
    """Where the Fertilizer Plan's rate comes from. A&M's own application
    notes appear in the plan, since some carry amounts (bermudagrass: 100 lb
    more N per extra hay cutting)."""
    basis = ("a %s yield goal of %s" % (crop.label, crop.yield_goal)
             if crop.yield_goal else crop.label)
    note = "Texas A&M rate for %s at %.0f ppm soil %s." % (basis, ppm, nutrient)
    suff = crop.sufficiency(nutrient)
    if suff is not None:
        note += " None needed at %.0f ppm or more." % suff
    return note + " "


_NUTRIENT_OF_FIX = {"nitrogen_deficient": "N", "phosphorus_deficient": "P",
                    "potassium_deficient": "K"}
_METRIC = {"N": "nitrogen", "P": "phosphorus", "K": "potassium"}


def _needs(crop, nutrient: str, sample: Dict) -> bool:
    """False only when the crop's A&M rate rounds to 0 lb/acre, where the
    Fertilizer Plan says none is needed. No A&M table: the item still stands."""
    rate = _am_rate(crop, nutrient, float(sample[_METRIC[nutrient]]))
    return rate is None or rate >= crop_profile.MIN_RATE


def _amount(crop, nutrient: str) -> str:
    """An N/P/K action item's amount. Every number lives in the Fertilizer Plan;
    without an A&M rate the item says what the plan says."""
    if crop is None:
        return "Pick a crop to get a Texas A&M rate."
    if not crop.has_table(nutrient):
        return crop.no_rate(nutrient)
    return crop_profile.SEE_PLAN


def _target_ph_note(crop) -> str:
    if crop is None or not crop.mapped:
        return ""
    (lo, hi), _ = crop.ph_band()
    return "Target pH %.1f-%.1f for %s. " % (lo, hi, crop.label)


# ---------------------------------------------------------------------------
# One builder per fix_code. Each takes the raw sample + the problem dict (+ the
# resolved crop, or None) and returns a fully specified recommendation. Rates
# are Texas-oriented.
# ---------------------------------------------------------------------------
def _fix_ph_acidic(s: Dict, p: Dict, crop=None) -> Dict:
    ph = float(s["ph"])
    # tons/acre of ag lime: the more acidic, the more lime.
    tons = max(1.0, min(4.5, agronomy._lin(ph, 6.0, 4.5, 1.0, 4.0)))
    low, high = tons * 25.0, tons * 40.0
    return dict(
        title="Raise pH with agricultural lime",
        action="Apply agricultural lime (calcitic; use dolomitic if Mg is also "
               "low) and incorporate 6 in deep.",
        rate=crop_profile.lab_rate("lime"),
        # Internal ballpark for the experimental cost/benefit only; not shown.
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="3-6 months before planting (fall for a spring crop) so it has "
               "time to react.",
        notes=_target_ph_note(crop) + "Re-test pH after one season; lime acts "
              "slowly. Sandy East-TX soils need less than Blackland clay.",
    )


def _fix_ph_alkaline(s: Dict, p: Dict, crop=None) -> Dict:
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
        rate=crop_profile.lab_rate("sulfur"),
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="Apply and incorporate several months ahead; oxidation to acid "
               "takes weeks-months and warm soil.",
        notes=_target_ph_note(crop) + "Test for free lime first. If the soil "
              "fizzes with acid it is calcareous and sulfur demand is very high "
              "-- prefer cotton, sorghum and other alkaline-tolerant crops.",
    )


def _fix_salinity(s: Dict, p: Dict, crop=None) -> Dict:
    ec = float(s["ec"])  # ECe
    lines = ["Leach salts with good-quality water once drainage is adequate "
             "(apply ~6 in extra water to push salts below the root zone)."]
    low, high = 20.0, 60.0   # leaching / water-management labour & water
    if ec > T.EC["lethal"]:
        lines.append("At this salinity, install tile/subsurface drainage and "
                     "reclaim over a season before expecting a crop.")
        low += 40; high += 120
    notes = ("Test your IRRIGATION water too -- Brazos water in the Fort Bend/"
             "Richmond reach runs saltier downstream, so over-irrigating with "
             "it adds salt. Meanwhile grow salt-tolerant cotton or sorghum.")
    if crop is not None and crop.mapped:
        notes = ("Yield loss for %s starts above ECe %.1f dS/m. "
                 % (crop.label, crop.profile()["ec_threshold_dS_m"]) + notes)
    return dict(
        title="Reduce salinity (leach + drain)",
        action=" ".join(lines),
        rate="Leaching fraction sized to soil; add drainage if water ponds.",
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="Pre-season, when you can move water through the profile.",
        notes=notes,
    )


def _fix_nitrogen(s: Dict, p: Dict, crop=None) -> Dict:
    n = float(s["nitrogen"])
    lb_n = _am_rate(crop, "N", n)
    # A&M's own N timing (e.g. wheat: 1/3 preplant, the rest before jointing)
    # when it has one without an amount in it; corn's side-dress split; else
    # the split that applies to any crop on leaching-prone ground.
    am_timing = crop.am_note("N") if crop is not None and crop.has_table("N") else None
    if am_timing and "lbs" in am_timing.lower():
        am_timing = None
    corn = crop is not None and crop.name == "Corn"
    if lb_n is not None:
        crop_note = _am_rate_note(crop, "N", n)
    else:
        crop_note = ""
        # Internal ballpark for the experimental cost/benefit only; not shown.
        lb_n = max(30.0, min(160.0, (DEFAULT_N_TARGET - n) * 4.0 + 30.0))
    low, high = lb_n * 0.55, lb_n * 0.75
    return dict(
        title="Correct nitrogen deficiency",
        action="Apply nitrogen fertilizer (urea 46-0-0). " + (
            "Split the application on corn to limit leaching." if corn else
            "On sandy or leaching-prone ground, split the application to limit "
            "leaching."),
        rate=_amount(crop, "N"),
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing=am_timing or (
            "At/near planting; split-apply for corn (pre-plant + side-dress)." if corn
            else "At/near planting; on sandy ground, split it (pre-plant + side-dress)."),
        notes=crop_note + "A legume cover crop can fix nitrogen biologically and "
              "cut this bill over time.",
    )


def _fix_phosphorus_def(s: Dict, p: Dict, crop=None) -> Dict:
    p_ppm = float(s["phosphorus"])
    p2o5 = _am_rate(crop, "P", p_ppm)
    if p2o5 is not None:
        crop_note = _am_rate_note(crop, "P", p_ppm)
    else:
        crop_note = ""
        p2o5 = max(30.0, min(70.0, (20.0 - p_ppm) * 3.0 + 30.0))  # cost/benefit only
    low, high = p2o5 * 0.60, p2o5 * 0.90
    return dict(
        title="Correct phosphorus deficiency",
        # Triple superphosphate, as in the Fertilizer Plan: DAP's nitrogen would
        # come on top of the plan's urea.
        action="Band phosphate (triple superphosphate 0-46-0) near the seed row "
               "for best early uptake.",
        rate=_amount(crop, "P"),
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="At planting (banded/starter).",
        notes=crop_note + "Banding beats broadcasting on high-pH soils where P "
              "gets tied up.",
    )


def _fix_phosphorus_excess(s: Dict, p: Dict) -> Dict:
    return dict(
        title="Stop over-applying phosphorus",
        action="Halt all P fertilizer and manure. Protect water: buffer strips, "
               "cover crops, and erosion control to stop P-laden runoff.",
        rate="None until soil test P drops below 150 ppm.",
        cost_per_acre_low=0.0, cost_per_acre_high=0.0,
        timing="Immediately, and ongoing.",
        notes="High P is a runoff / water-quality liability, not a yield gain. "
              "Draw it down through crop removal over several seasons.",
    )


def _fix_potassium(s: Dict, p: Dict, crop=None) -> Dict:
    k = float(s["potassium"])
    k2o = _am_rate(crop, "K", k)
    if k2o is not None:
        crop_note = _am_rate_note(crop, "K", k)
    else:
        thr = T.POTASSIUM["deficient"]
        k2o = max(40.0, min(120.0, (thr - k) * 1.2 + 40.0))  # cost/benefit only
        crop_note = "K deficiency threshold is {:.0f} ppm. ".format(thr)
    low, high = k2o * 0.45, k2o * 0.70
    return dict(
        title="Correct potassium deficiency",
        action="Apply potash (muriate of potash 0-0-60). Split applications on "
               "sandy or leaching-prone ground.",
        rate=_amount(crop, "K"),
        cost_per_acre_low=low, cost_per_acre_high=high,
        timing="Pre-plant or at planting.",
        notes=crop_note + "Sandy ground holds less K than clay, so the sandier "
              "the field the more frequent and smaller the doses should be.",
    )


def assess_target_crop(sample: Dict, target_crop=None) -> Dict:
    """Report soil limits for the selected crop (a form crop name or Crop).

    A crop with a Texas A&M table is judged on its pH bands and salt tolerance,
    by the same test as the "viable now" list (agronomy.crop_fit); `fit` is
    "ideal", "viable_with_adjustments" or "not_viable". A crop with only an
    older FarmersFirst viability profile (T.CROPS) uses that. A custom label
    does not imply known thresholds or a new ML crop class.
    """
    crop = crop_profile.as_crop(target_crop)
    if crop is not None and crop.mapped:
        fit = agronomy.crop_fit(sample, crop)
        be = "are" if crop_profile.is_plural(crop.name) else "is"
        notes = {
            "ideal": "pH and salinity suit %s." % crop.label,
            "viable_with_adjustments": "%s %s viable with adjustments; review these "
                                       "before planting." % (crop.name, be),
            "not_viable": "%s %s not viable on these readings until the limits below "
                          "are fixed." % (crop.name, be),
        }
        return {
            "profile_name": crop.name, "profile_available": True,
            "fit": fit["status"], "within_profile": fit["status"] == "ideal",
            "limiting_factors": fit["limiting_factors"] + fit["adjustments"],
            "yield_goal": crop.yield_goal, "am_table": crop.table, "basis": crop.basis(),
            "note": notes[fit["status"]],
        }
    name = crop.name if crop else None
    if name not in T.CROPS:
        return {
            "profile_name": None, "profile_available": False,
            "within_profile": None, "limiting_factors": [],
            "yield_goal": None, "am_table": None, "basis": None,
            "note": ("No crop-specific profile is available for %s. Recommendations "
                     "use general soil guidance; confirm crop requirements with "
                     "your soil lab." % name) if name else "Crop was not recorded for this sample.",
        }
    viability = agronomy.farmersfirst_crop_viability(sample)
    limits = next((item["limiting_factors"] for item in viability["not_viable"]
                   if item["crop"] == name), [])
    return {
        "profile_name": name, "profile_available": True,
        "within_profile": not limits, "limiting_factors": limits,
        "yield_goal": None, "am_table": None, "basis": None,
        "note": (("Readings meet the existing %s soil profile." % name) if not limits
                 else "Review these soil limits for %s before planting or applying amendments." % name),
    }


def build_recommendations(sample: Dict,
                          problems: Optional[List[Dict]] = None,
                          target_crop=None) -> List[Dict]:
    """Map detected problems to concrete fixes, worst-first, de-duplicated.

    `target_crop` is a form crop name or a resolved crop_profile.Crop. Every
    returned dict carries an amount (`rate`) and timing.
    """
    crop = crop_profile.as_crop(target_crop)
    if problems is None:
        problems = agronomy.detect_problems(sample, crop)

    recs: List[Dict] = []
    seen_fix_codes = set()
    for prob in problems:
        fc = prob.get("fix_code")
        if fc is None or fc in seen_fix_codes:
            continue
        seen_fix_codes.add(fc)

        if fc == "ph_acidic":
            rec = _fix_ph_acidic(sample, prob, crop)
        elif fc == "ph_alkaline":
            rec = _fix_ph_alkaline(sample, prob, crop)
        elif fc == "salinity":
            rec = _fix_salinity(sample, prob, crop)
        elif fc == "nitrogen_deficient":
            rec = _fix_nitrogen(sample, prob, crop)
        elif fc == "phosphorus_deficient":
            rec = _fix_phosphorus_def(sample, prob, crop)
        elif fc == "phosphorus_excess":
            rec = _fix_phosphorus_excess(sample, prob)
        elif fc == "potassium_deficient":
            rec = _fix_potassium(sample, prob, crop)
        else:
            continue
        nutrient = _NUTRIENT_OF_FIX.get(fc)
        if nutrient and not _needs(crop, nutrient, sample):
            continue  # the A&M rate rounds to 0 lb: the plan says none needed

        rec.update({
            "problem_code": prob["code"],
            "fix_code": fc,
            "severity": prob["severity"],
            "priority_weight": prob["penalty"],
            "addresses": prob["message"],
        })
        recs.append(rec)

    # A&M recommends N for most crops even when soil N is fine. That rate is
    # routine crop feeding, not a soil problem, but the farmer still needs it.
    if ("nitrogen_deficient" not in seen_fix_codes
            and crop is not None and crop.has_table("N") and _needs(crop, "N", sample)):
        rec = _fix_nitrogen(sample, None, crop)
        rec.update({
            "title": "Apply nitrogen for %s" % crop.label,
            "problem_code": None,
            "fix_code": "nitrogen_routine",
            "severity": "routine",
            "priority_weight": 0.0,
            "addresses": "Routine Texas A&M nitrogen rate for %s; soil N is not low."
                         % crop.label,
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
