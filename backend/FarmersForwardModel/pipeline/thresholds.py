"""Single source of truth for Texas-specific soil thresholds and crop viability.

Every other module imports its numbers from here. Do not hard-code thresholds
elsewhere -- change them once, here, and the whole pipeline follows.

Values are the FarmersFirst Texas thresholds (Fort Bend County / Brazos River
region), NOT generic textbook numbers.

The pipeline measures exactly five soil properties: pH, EC (salinity), and the
three primary macronutrients N / P / K. Everything downstream keys off these.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

# ---------------------------------------------------------------------------
# Canonical measurement columns (the schema a farm CSV is expected to provide).
# Wholly-missing columns get sensible defaults during preprocessing.
# ---------------------------------------------------------------------------
MEASUREMENT_COLUMNS = [
    "ph",
    "ec",              # electrical conductivity, dS/m (salinity)
    "nitrogen",        # ppm (NO3-N)
    "phosphorus",      # ppm
    "potassium",       # ppm
]

ID_COLUMNS = ["sample_id", "latitude", "longitude", "region", "zone"]

# Our EC sensor reads a 1:2 soil:water extract. The ML training data and every
# EC threshold below are ECe (saturated paste), so readings are converted once
# where they enter the app, using crop_data/crop_lookup.ECE_FACTOR.
SENSOR_EC_METHOD = "1:2"

# ---------------------------------------------------------------------------
# Generic Texas soil thresholds (used exactly as specified by the domain team).
# Samples whose crop maps to a Texas A&M table (CROP_DEFAULT_YIELD) are judged
# against that crop's pH, salinity and N/P/K numbers instead; these apply to
# unmapped crops, samples without a crop, and the ML training labels.
# ---------------------------------------------------------------------------
PH = {
    "ideal_low": 6.0,
    "ideal_high": 7.0,   # optimal band 6.0-7.0; distance outside is penalized
    "alert_low": 5.5,    # < 5.5 = alert (acidic)
    "alert_high": 7.8,   # > 7.8 = alert (alkaline)
}

EC = {                  # dS/m
    "stress": 2.0,      # > 2   = salinity stress
    "severe": 4.0,      # > 4   = severe
    "lethal": 8.0,      # > 8   = lethal to most crops
}

NITROGEN = {"deficient": 20.0}  # ppm

# A&M recommends N at almost any soil level, so a crop's N rate alone is not a
# soil problem. Soil N counts as low only when the rate is at least this share
# of the crop's rate at 0 ppm; below it, the rate is routine crop feeding.
AM_N_PROBLEM_FRACTION = 0.75

# User-specified annual mineralization approximation:
# OM% * 20,000 * 0.05 * 0.03 = OM% * 30 lb N/acre/year;
# dividing by 2 lb/acre per ppm gives an N-credit proxy of OM% * 15 ppm.
N_CREDIT_PPM_PER_OM_PCT = 15.0

# USDA NRCS National Soil Survey Handbook, Part 618, organic matter measurement.
# An estimated conversion, not a measured organic-matter percentage.
ORGANIC_CARBON_TO_OM_FACTOR = 1.724

PHOSPHORUS = {
    "deficient": 15.0,          # ppm
    "excess": 150.0,            # > 150 = over-application / runoff risk
}

POTASSIUM = {
    "deficient": 100.0,         # ppm  (< 100 = deficient)
}

# ---------------------------------------------------------------------------
# Health-score categories (score is 0-100, clamped)
# ---------------------------------------------------------------------------
# Ordered low -> high. (name, inclusive_low_bound)
CATEGORY_BANDS = [
    ("Critical", 0),
    ("Poor", 20),
    ("Fair", 40),
    ("Good", 60),
    ("Excellent", 80),
]
CATEGORY_NAMES = [name for name, _ in CATEGORY_BANDS]

# A crop-specific score is capped here when pH is outside that crop's
# tolerable band, so it reads Fair at best. Scores display as whole numbers,
# so the cap is 59 rather than 59.9 (which would show as "60").
PH_OUT_OF_TOLERANCE_SCORE_CAP = 59.0


def categorize(score: float) -> str:
    """Map a 0-100 health score to a health category name."""
    score = max(0.0, min(100.0, float(score)))
    label = CATEGORY_BANDS[0][0]
    for name, low in CATEGORY_BANDS:
        if score >= low:
            label = name
    return label


# ---------------------------------------------------------------------------
# FarmersFirst crop viability rules: pH in range, EC <= ec_max, N/P/K >=
# minimums. These label the ML crop classifier's training data
# (agronomy.crop_viability_vector), so changing them means retraining. The
# "viable now" list on reports uses the A&M crop profiles below instead.
# ---------------------------------------------------------------------------

# "Viable now" on reports: pH inside the crop's tolerable band and at least
# this Maas-Hoffman relative yield from salinity. Viable crops are "ideal" when
# pH is in the optimal band with no salt yield loss, else "viable with
# adjustments".
VIABLE_MIN_RELATIVE_YIELD = 80.0  # percent

# crop -> dict(ph_min, ph_max, ec_max, n_min, p_min, k_min)
CROPS = {
    "Corn":        {"ph_min": 5.8, "ph_max": 7.0, "ec_max": 1.7, "n_min": 30, "p_min": 20, "k_min": 120},
    "Cotton":      {"ph_min": 5.8, "ph_max": 8.0, "ec_max": 7.7, "n_min": 25, "p_min": 15, "k_min": 100},
    "Sorghum":     {"ph_min": 5.5, "ph_max": 7.5, "ec_max": 6.8, "n_min": 20, "p_min": 15, "k_min": 100},
    "Soybeans":    {"ph_min": 6.0, "ph_max": 7.0, "ec_max": 5.0, "n_min": 20, "p_min": 20, "k_min": 110},
    "Wheat":       {"ph_min": 5.5, "ph_max": 7.5, "ec_max": 6.0, "n_min": 25, "p_min": 15, "k_min": 100},
    "Tomatoes":    {"ph_min": 5.5, "ph_max": 7.0, "ec_max": 2.5, "n_min": 30, "p_min": 25, "k_min": 150},
    "Peppers":     {"ph_min": 6.0, "ph_max": 7.0, "ec_max": 1.5, "n_min": 25, "p_min": 20, "k_min": 130},
    "Blueberries": {"ph_min": 4.5, "ph_max": 5.5, "ec_max": 1.0, "n_min": 15, "p_min": 10, "k_min": 80},
    "Strawberries":{"ph_min": 5.5, "ph_max": 6.5, "ec_max": 1.0, "n_min": 20, "p_min": 15, "k_min": 100},
    "Hemp":        {"ph_min": 6.0, "ph_max": 7.0, "ec_max": 3.0, "n_min": 30, "p_min": 20, "k_min": 120},
}
CROP_NAMES = list(CROPS.keys())

# ---------------------------------------------------------------------------
# Assessment-form crop -> its table in crop_data/crop_npk_tables.json, which
# supplies the crop's pH range, salt tolerance and fertilizer rates. These are
# also the crops judged for "viable now". Reports name the basis: the yield
# goal where A&M has one table per yield goal (a mid-range default that
# pipeline.crop_profile.resolve() lets a yield_goal override), or the table
# itself where it is one of several A&M variants picked as the default
# (assumed=True). Hemp has no A&M table and stays generic.
# ---------------------------------------------------------------------------
class CropTable(NamedTuple):
    category: str                      # A&M category, e.g. "row"
    table: str                         # A&M table, e.g. "CORN (150 BU/A)"
    yield_goal: Optional[str] = None   # shown on reports, e.g. "150 bu/A"
    assumed: bool = False              # default pick among A&M variants


CROP_DEFAULT_YIELD = {
    "Corn":           CropTable("row", "CORN (150 BU/A)", "150 bu/A"),
    "Cotton":         CropTable("fiber", "COTTON (2.0 BALES/A)", "2.0 bales/A"),
    "Sorghum":        CropTable("row", "GRAIN SORGHUM (5000 LBS/A)", "5,000 lb/A"),
    "Wheat":          CropTable("row", "WHEAT (40-59 BU/A) GRAIN ONLY", "40-59 bu/A, grain only"),
    "Rice":           CropTable("row", "RICE (SHORT VARIETIES 6,000-10,000 LBS/ACRE)",
                                "6,000-10,000 lb/A, short varieties"),
    "Barley":         CropTable("row", "BARLEY GRAIN (30-40 BU/A)", "30-40 bu/A"),
    "Canola":         CropTable("oil", "CANOLA (1500 LBS/A)", "1,500 lb/A"),
    "Rye":            CropTable("row", "RYE , GRAIN"),
    "Soybeans":       CropTable("oil", "SOYBEANS"),
    "Sunflowers":     CropTable("oil", "SUNFLOWERS"),
    "Sugarcane":      CropTable("veg", "SUGARCANE"),
    "Sweet potatoes": CropTable("veg", "SWEET POTATOES"),
    "Tomatoes":       CropTable("veg", "TOMATOES"),
    "Watermelons":    CropTable("veg", "WATERMELONS"),
    "Cucumbers":      CropTable("veg", "CUCUMBERS"),
    "Okra":           CropTable("veg", "OKRA"),
    "Blueberries":    CropTable("veg", "BLUEBERRY"),
    "Strawberries":   CropTable("veg", "STRAWBERRIES (FIELD PLANTING)"),
    "Peppers":        CropTable("veg", "PEPPERS,BELL", assumed=True),
    "Potatoes":       CropTable("veg", "POTATOES (EARLY VARIETY)", assumed=True),
    "Peanuts":        CropTable("oil", "PEANUTS , DRYLAND", assumed=True),
    "Squash":         CropTable("veg", "SQUASH , SUMMER", assumed=True),
    "Oats":           CropTable("forage", "OATS (MODERATE GRAZING)", assumed=True),
    "Alfalfa":        CropTable("forage", "ALFALFA (NON-IRRIGATED , ANNUALLY)", assumed=True),
    "Pecans":         CropTable("veg", "PECANS (UPLAND GROVES)", assumed=True),
    "Bermudagrass":   CropTable("forage", "IMPROVED AND HYBRID BERMUDA GRASS "
                                "(2 HAY CUTTINGS-2 TONS/A AVG.)", assumed=True),  # also "Hay"
}
