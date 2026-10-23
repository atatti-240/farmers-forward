"""Passive fertilizer calculator: Texas A&M rates -> products, totals and bags.

Modelled on the Texas A&M AgriLife "Agricultural Fertilizer Management
Calculator" (AgFertCalc 2023, v1.2): products are applied in order, each at the
rate that meets the first nutrient it would otherwise over-apply, and later
products cover only what is still missing. With the default single-nutrient
products in fert_products.json every product hits its A&M rate exactly.

The nutrient rates come only from the region's A&M table, through
pipeline.crop_profile.assess() -- the lookup that fills crop_assessment and the
recommender's rates -- so the plan cannot disagree with either. Everything
else is arithmetic on those rates. pH and salinity get a warning and a referral
to the A&M lab, never an invented lime, sulfur or salinity rate.

The plan carries its own display text, so the results page and the PDF word it
identically.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fert_products.json")

NUTRIENTS = ("N", "P", "K")
NUTRIENT_NAMES = {"N": "nitrogen (N)", "P": "phosphate (P2O5)", "K": "potash (K2O)"}
LAB_REFERRAL = "the Texas A&M Soil Testing Lab or your county AgriLife agent"
# Larger than any farm; keeps totals and bag counts finite.
MAX_ACRES = 1_000_000
# Pounds per acre left over from float arithmetic count as met.
_MET = 0.05


def load_products(path: str = CONFIG_PATH) -> Dict:
    """The product list and bag size from fert_products.json, checked."""
    with open(path) as f:
        raw = json.load(f)
    bag_lb = float(raw["bag_lb"])
    if not (math.isfinite(bag_lb) and bag_lb > 0):
        raise ValueError("bag_lb must be a positive number")
    products = []
    for p in raw["products"]:
        parts = [float(x) for x in str(p["grade"]).split("-")]
        if len(parts) != 3 or not any(parts) or min(parts) < 0 or max(parts) > 100:
            raise ValueError("%s: grade must look like 46-0-0" % p["name"])
        price = p.get("price_per_ton")
        if price is not None:
            price = float(price)
            if not (math.isfinite(price) and price >= 0):
                raise ValueError("%s: price_per_ton must be a non-negative number" % p["name"])
        products.append({
            "name": p["name"],
            "grade": "-".join("%g" % x for x in parts),
            "fractions": dict(zip(NUTRIENTS, (x / 100.0 for x in parts))),
            "price_per_ton": price,
        })
    return {"bag_lb": bag_lb, "products": products}


PRODUCTS = load_products()


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------
def _lbs(x: float) -> str:
    """Whole pounds, rounded as the recommender rounds its "~X lb urea"."""
    text = "{:,.0f}".format(x)
    return text + (" lb" if text == "1" else " lbs")


def _half_up(x: float) -> int:
    """Whole pounds for a total, rounding .5 up as a farmer would by hand."""
    return int(math.floor(x + 0.5))


def _num(x: float) -> str:
    """A reading as entered: up to two decimals, at least one (5.2, 5.77)."""
    text = ("%.2f" % x).rstrip("0")
    return text + "0" if text.endswith(".") else text


def _money(x: float) -> str:
    return "${:,.2f}".format(x)


def _count(n: int, noun: str) -> str:
    return "%d %s%s" % (n, noun, "" if n == 1 else "s")


def _bags(total_lb: float, bag_lb: float) -> int:
    """Whole bags needed to cover total_lb (rounding guards float fuzz)."""
    return math.ceil(round(total_lb / bag_lb, 6)) if total_lb > 0 else 0


def _area(acres) -> Optional[float]:
    """Acres rounded to 0.01 (0.0 for a sample outside the drawn field), or
    None when unknown or unusable."""
    try:
        area = round(float(acres), 2)
    except (TypeError, ValueError):
        return None
    return area if math.isfinite(area) and 0 <= area <= MAX_ACRES else None


def _source(crop, assessment: Dict) -> str:
    table = re.sub(r"\s+,", ",", assessment["crop"])
    if crop.yield_goal:
        return ("Source: Texas A&M AgriLife %s table, yield goal %s (default)"
                % (table, crop.yield_goal))
    if crop.assumed:
        return "Source: Texas A&M AgriLife %s table (default table for %s)" % (table, crop.label)
    return "Source: Texas A&M AgriLife %s table (the only A&M table for %s)" % (table, crop.label)


def _ph_warning(ph: Optional[Dict], crop_label: str) -> Optional[str]:
    if not ph or ph.get("status") in (None, "optimal"):
        return None
    value = ph.get("ph")
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    lo, hi = ph["optimal"]
    acidic = ph["status"] in ("slightly_low", "low")
    return ("Soil pH %s is %s %s for %s (target %.1f–%.1f). Get a %s rate from %s."
            % (_num(value), "a little" if ph["status"].startswith("slightly") else "too",
               "low (acidic)" if acidic else "high (alkaline)", crop_label, lo, hi,
               "lime" if acidic else "sulfur", LAB_REFERRAL))


def _salinity_warning(sal: Optional[Dict], crop_label: str) -> Optional[str]:
    if not sal or sal.get("status") in (None, "ok"):
        return None
    text = ("Salt level is %s for %s: about %s dS/m (estimated saturated-paste EC), "
            "and yield loss starts above %s dS/m."
            % ("a little high" if sal["status"] == "minor_loss" else "too high", crop_label,
               _num(sal["ece_dS_m"]), _num(sal["threshold"])))
    yield_pct = sal.get("relative_yield_pct")
    if yield_pct == 0:
        text += " Expect little or no yield."
    elif yield_pct is not None:
        text += " Expect about %.0f%% of a normal yield." % yield_pct
    return text + " Get a salinity treatment plan from %s." % LAB_REFERRAL


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------
def _apply_products(rates: Dict[str, Optional[float]], products: List[Dict]):
    """AgFertCalc's rule: each product, in order, at the largest rate that
    over-applies nothing, against what earlier products left. Returns
    ([(product, lbs product/acre, {nutrient: lbs supplied/acre}, nutrient that
    set the rate)], {nutrient: lbs/acre still missing})."""
    remaining = {n: r for n, r in rates.items() if r is not None}
    applied = []
    for prod in products:
        supplies = {n: f for n, f in prod["fractions"].items() if f > 0 and n in remaining}
        if not supplies:
            continue
        rate, lead = min((remaining[n] / f, n) for n, f in supplies.items())
        if rate * supplies[lead] <= _MET:
            continue
        given = {n: rate * f for n, f in supplies.items()}
        for n, lbs in given.items():
            remaining[n] = max(0.0, remaining[n] - lbs)
        applied.append((prod, rate, given, lead))
    return applied, remaining


def _product_item(prod: Dict, rate: float, given: Dict[str, float], lead: str,
                  area: Optional[float], bag_lb: float) -> Dict:
    shown = round(rate)  # whole lbs/acre, the same rounding as "~X lb urea"
    per_acre = shown if shown >= 1 else rate
    parts = ["%s/acre" % _lbs(shown) if shown >= 1 else "less than 1 lb/acre"]
    total = bags = cost_acre = cost_total = None
    if area:
        # From the rounded rate, as AgFertCalc prices its rounded rate, and in
        # whole pounds, so the numbers on the page multiply and add up.
        total = _half_up(per_acre * area)
        bags = _bags(total, bag_lb)
        parts += ["%s total" % _lbs(total), _count(bags, "bag")]
    price = prod["price_per_ton"]
    if price is not None:
        cost_acre = round(per_acre * price / 2000.0, 2)
        parts.append("%s/acre" % _money(cost_acre))
        if total is not None:
            cost_total = round(total * price / 2000.0, 2)
            parts.append("%s total" % _money(cost_total))
    # A&M rates carry 0.1 lb; rounding there first keeps (rate / 0.46) * 0.46
    # from tipping a 62.5 to 63 where the recommendation card says 62.
    supplied = " + ".join("%s %s" % (_lbs(round(given[n], 1)), NUTRIENT_NAMES[n])
                          for n in NUTRIENTS if n in given)
    return {
        "kind": "product", "nutrient": lead,
        "product": prod["name"], "grade": prod["grade"],
        "label": "%s (%s)" % (prod["name"], prod["grade"]),
        "amounts": " · ".join(parts),
        "detail": "Supplies %s per acre" % supplied,
        "lb_per_acre": shown, "total_lb": total, "bags": bags,
        "cost_per_acre": cost_acre, "cost_total": cost_total,
    }


def build_plan(crop_type: Optional[str], crop, assessment: Optional[Dict],
               acres=None, config: Optional[Dict] = None) -> Dict:
    """The fertilizer plan for one region.

    `crop` is the region's pipeline.crop_profile.Crop and `assessment` its
    crop_profile.assess() readout (None when the crop has no A&M table).
    `acres` is the region's share of the field, or None when unknown.
    """
    config = config or PRODUCTS
    bag_lb = config["bag_lb"]
    area = _area(acres)
    if area:
        area_text = "%s acres · %g-lb bags" % ("{:,.2f}".format(area), bag_lb)
    elif area == 0:
        area_text = ("This sample covers none of the drawn field, so amounts are "
                     "per acre only.")
    else:
        area_text = "Field area unknown for this sample, so amounts are per acre only."
    plan = {
        "available": assessment is not None,
        "area_acres": area,
        "bag_lb": bag_lb,
        "area_text": area_text,
        "items": [], "warnings": [], "notes": [], "source": None, "message": None,
        "nutrients": {},
    }
    if assessment is None:
        plan["message"] = ("No Texas A&M fertilizer table for %s, so no amounts are "
                           "calculated. Ask %s for a rate."
                           % (crop_type or "this crop", LAB_REFERRAL))
        return plan

    rates: Dict[str, Optional[float]] = {}
    for n in NUTRIENTS:
        add = (assessment.get(n) or {}).get("add")
        per_acre = str((assessment.get("units") or {}).get(n, "")).endswith("/acre")
        rates[n] = float(add) if add is not None and per_acre else None
    plan["nutrients"] = rates
    # A rate that rounds to 0 lb (A&M interpolates to 0.1 lb) needs no product,
    # just as the recommendation card would print it as "0 lb".
    needs = {n: (r if r is None or r >= 0.5 else 0.0) for n, r in rates.items()}

    applied, remaining = _apply_products(needs, config["products"])
    for n in NUTRIENTS:
        note = crop.am_note(n)
        if note:  # "http://... Follow the Texas Rice ..." -> "Follow ... (http://...)"
            note = re.sub(r"^(https?://\S+)\s+(.+)$", r"\2 (\1)", note)
        if needs[n] is None:
            plan["items"].append({
                "kind": "no_rate", "nutrient": n,
                "text": "No Texas A&M %s rate for %s.%s" % (
                    NUTRIENT_NAMES[n], crop.label, " A&M note: %s" % note if note else "")})
            continue
        if needs[n] <= 0:
            plan["items"].append({"kind": "none_needed", "nutrient": n,
                                  "text": "No %s needed" % NUTRIENT_NAMES[n]})
            continue
        plan["items"] += [_product_item(prod, rate, given, lead, area, bag_lb)
                          for prod, rate, given, lead in applied if lead == n]
        if note:
            plan["notes"].append("Texas A&M note on %s: %s" % (NUTRIENT_NAMES[n], note))
    for n in NUTRIENTS:
        if remaining.get(n, 0) > _MET:
            plan["items"].append({
                "kind": "uncovered", "nutrient": n,
                "text": "Still needed: %s %s per acre. None of the set products can "
                        "supply it without over-applying another nutrient."
                        % (_lbs(remaining[n]), NUTRIENT_NAMES[n])})

    plan["warnings"] = [w for w in (_ph_warning(assessment.get("ph"), crop.label),
                                    _salinity_warning(assessment.get("salinity"), crop.label))
                        if w]
    plan["source"] = _source(crop, assessment)
    return plan


def plan_for_region(crop_type: Optional[str], *, nitrogen: float, phosphorus: float,
                    potassium: float, ph: float, ece: float, acres=None,
                    config: Optional[Dict] = None) -> Dict:
    """build_plan() from raw readings: soil N/P/K in ppm, pH, and EC already
    converted to ECe (dS/m), plus the region's acres."""
    from backend import model_runner
    model_runner._ensure_pipeline_path()
    from pipeline import crop_profile
    crop = crop_profile.resolve(crop_type)
    sample = dict(ph=ph, ec=ece, nitrogen=nitrogen, phosphorus=phosphorus,
                  potassium=potassium)
    return build_plan(crop_type, crop, crop_profile.assess(sample, crop), acres, config)


def field_total(plans: Iterable[Tuple[str, Optional[Dict]]]) -> Dict:
    """Whole-field totals per product from (section_label, plan) pairs.

    Bags are counted on each product's field total, not summed per region.
    Regions without an A&M table or without an area are listed, not counted.
    """
    rows: Dict[str, Dict] = {}
    acres, n_regions, excluded = 0.0, 0, []
    bag_lb = PRODUCTS["bag_lb"]
    for label, plan in plans:
        if not plan:
            continue
        bag_lb = plan.get("bag_lb") or bag_lb
        if not plan.get("available"):
            excluded.append("%s: no Texas A&M table for this crop, so no amounts." % label)
            continue
        if plan.get("area_acres") is None:
            excluded.append("%s: no field area, so it is shown per acre only." % label)
            continue
        if not plan["area_acres"]:
            excluded.append("%s: covers none of the drawn field, so it adds nothing." % label)
            continue
        acres += plan["area_acres"]
        n_regions += 1
        for item in plan["items"]:
            if item["kind"] == "no_rate":
                excluded.append("%s: no Texas A&M %s rate for this crop, so none is counted."
                                % (label, NUTRIENT_NAMES[item["nutrient"]]))
            if item["kind"] != "product":
                continue
            row = rows.setdefault(item["label"], {"label": item["label"], "total_lb": 0,
                                                  "cost_total": 0.0})
            row["total_lb"] += item["total_lb"]
            if row["cost_total"] is not None and item["cost_total"] is not None:
                row["cost_total"] += item["cost_total"]
            else:
                row["cost_total"] = None

    products = []
    for row in rows.values():
        total = row["total_lb"]  # whole pounds: the sum of the region totals shown
        bags = _bags(total, bag_lb)
        cost = round(row["cost_total"], 2) if row["cost_total"] is not None else None
        parts = [_lbs(total), _count(bags, "bag")]
        if cost is not None:
            parts.append(_money(cost))
        products.append({"label": row["label"], "total_lb": total, "bags": bags,
                         "cost_total": cost, "amounts": " · ".join(parts)})
    acres = round(acres, 2)
    return {
        "acres": acres,
        "n_regions": n_regions,
        "bag_lb": bag_lb,
        "products": products,
        "excluded": excluded,
        "summary": ("%s acres across %s. Each sample covers the part of the field "
                    "closest to it; bags are %g lb." % ("{:,.2f}".format(acres),
                                                       _count(n_regions, "sample"), bag_lb)
                    if n_regions else "No samples with a field area and an A&M table."),
        "none_needed": n_regions > 0 and not products,
    }
