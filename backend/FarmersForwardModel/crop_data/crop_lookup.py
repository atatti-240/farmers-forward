"""
Crop-specific soil lookups for Farmers Forward.

Data files (same folder):
  crop_npk_tables.json      - Texas A&M N/P/K fertilizer tables, digitized (all 24 PDFs)
  crop_ph_ec_profiles.json  - crop pH ranges + salt tolerance (Maas-Hoffman), mapped to every A&M crop

Quick use:
    from crop_lookup import fert_rec, ph_status, salinity_status, assess
    fert_rec("row", "CORN (150 BU/A)", "P", 18)      -> 64.0  (lbs P2O5/acre)
    ph_status("row", "CORN (150 BU/A)", 8.1)         -> {"status": "high", ...}
    salinity_status("row", "CORN (150 BU/A)", ece=3.2) -> {"relative_yield_pct": 82.0, ...}
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "crop_npk_tables.json")) as f:
    NPK = json.load(f)["crops"]
with open(os.path.join(_HERE, "crop_ph_ec_profiles.json")) as f:
    _pe = json.load(f)
    PROFILES = _pe["profiles"]
    CROP_TO_PROFILE = _pe["crop_to_profile"]

# Rough ECe conversion factors (texture-dependent!). Calibrate against a few
# lab saturated-paste results before trusting these for real recommendations.
ECE_FACTOR = {"ece": 1.0, "1:1": 2.0, "1:2": 3.0, "1:5": 6.4}  # 1:2 = team method; 3.0 is an estimate for Gulf Coast clays — replace with our own calibrated factor


def list_crops(category=None):
    cats = [category] if category else NPK.keys()
    return [(c, name) for c in cats for name in NPK[c]]


def _crop(category, crop):
    try:
        return NPK[category][crop]
    except KeyError:
        raise KeyError(f"Unknown crop '{crop}' in category '{category}'. Use list_crops().")


def fert_rec(category, crop, nutrient, soil_ppm):
    """lbs of nutrient to ADD (N as N, P as P2O5, K as K2O) for a soil-test ppm.
    Linear interpolation between A&M columns; holds last value past the table.
    Returns None if A&M gives no table (e.g. rice N)."""
    tbl = _crop(category, crop).get(nutrient)
    if not tbl:
        return None
    pts = sorted((float(k), float(v)) for k, v in tbl.items())
    if soil_ppm <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if soil_ppm <= x1:
            return round(y0 + (y1 - y0) * (soil_ppm - x0) / (x1 - x0), 1)
    return pts[-1][1]


def sufficiency_ppm(category, crop, nutrient):
    """Soil-test ppm where A&M stops recommending fertilizer (None = never, typical for N)."""
    return _crop(category, crop).get("sufficiency_ppm", {}).get(nutrient)


def profile(category, crop):
    return PROFILES[CROP_TO_PROFILE[f"{category}/{crop}"]]


def ph_status(category, crop, ph):
    p = profile(category, crop)
    (olo, ohi), (tlo, thi) = p["ph_opt"], p["ph_tol"]
    if olo <= ph <= ohi:
        s = "optimal"
    elif tlo <= ph < olo:
        s = "slightly_low"
    elif ohi < ph <= thi:
        s = "slightly_high"
    elif ph < tlo:
        s = "low"
    else:
        s = "high"
    return {"status": s, "ph": ph, "optimal": p["ph_opt"], "tolerable": p["ph_tol"],
            "confidence": p["ph_confidence"], "note": p["note"]}


def salinity_status(category, crop, ec=None, ece=None, method="ece"):
    """Pass ece directly, or ec + method ('1:1', '1:2' or '1:5') to convert.
    Returns Maas-Hoffman relative yield and a status."""
    if ece is None:
        ece = ec * ECE_FACTOR[method]
    p = profile(category, crop)
    thr, slope = p["ec_threshold_dS_m"], p["ec_slope_pct_per_dS_m"]
    if ece <= thr:
        ry, s = 100.0, "ok"
    elif slope is None:  # rating-only crops: no slope published
        ry, s = None, "above_threshold"
    else:
        ry = max(0.0, round(100 - slope * (ece - thr), 1))
        s = "minor_loss" if ry >= 90 else "moderate_loss" if ry >= 75 else "severe_loss"
    return {"status": s, "ece_dS_m": round(ece, 2), "threshold": thr, "relative_yield_pct": ry,
            "salt_class": p["salt_class"], "data_source": p["ec_source"]}


def assess(category, crop, ph=None, ece=None, ec=None, ec_method="ece", n_ppm=None, p_ppm=None, k_ppm=None):
    """One call for the whole crop-specific readout."""
    out = {"crop": crop, "category": category, "units": _crop(category, crop).get("units")}
    if ph is not None:
        out["ph"] = ph_status(category, crop, ph)
    if ece is not None or ec is not None:
        out["salinity"] = salinity_status(category, crop, ec=ec, ece=ece, method=ec_method)
    for nut, val in (("N", n_ppm), ("P", p_ppm), ("K", k_ppm)):
        if val is not None:
            out[nut] = {"soil_ppm": val, "add": fert_rec(category, crop, nut, val),
                        "sufficiency_ppm": sufficiency_ppm(category, crop, nut)}
    notes = _crop(category, crop).get("notes")
    if notes:
        out["am_notes"] = notes
    return out


if __name__ == "__main__":
    import pprint
    pprint.pprint(assess("row", "CORN (150 BU/A)", ph=8.1, ec=1.0, ec_method="1:2", n_ppm=8, p_ppm=18, k_ppm=95))
