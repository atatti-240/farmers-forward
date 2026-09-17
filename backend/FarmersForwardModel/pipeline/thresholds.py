"""Single source of truth for Texas-specific soil thresholds and crop viability.

Every other module imports its numbers from here. Do not hard-code thresholds
elsewhere -- change them once, here, and the whole pipeline follows.

Values are the FarmersFirst Texas thresholds (Fort Bend County / Brazos River
region), NOT generic textbook numbers.

The pipeline measures exactly five soil properties: pH, EC (salinity), and the
three primary macronutrients N / P / K. Everything downstream keys off these.
"""

from __future__ import annotations

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

# ---------------------------------------------------------------------------
# Texas soil thresholds  (used exactly as specified by the domain team)
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


def categorize(score: float) -> str:
    """Map a 0-100 health score to a health category name."""
    score = max(0.0, min(100.0, float(score)))
    label = CATEGORY_BANDS[0][0]
    for name, low in CATEGORY_BANDS:
        if score >= low:
            label = name
    return label


# ---------------------------------------------------------------------------
# Crop viability thresholds. A crop is viable "right now" when the current
# reading satisfies ALL of: pH in range, EC <= ec_max, N/P/K >= minimums.
# ---------------------------------------------------------------------------
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
