"""Cleaning, validation, feature engineering, and the train/predict data contract.

`FEATURE_COLUMNS` is the one list the models are trained on and predicted from --
`build_features` is called in exactly the same way in train.py and predict.py so
the two can never drift apart.

The measurement schema is exactly five soil properties:

    sample_id, latitude, longitude, region, zone, ph, ec, nitrogen, phosphorus, potassium

Real farm CSVs in `data/farm_data/` are read by train.py in real/combined modes.
Training excludes incomplete real rows before this imputation-based cleaner.
Prediction and the legacy load_real_farm_data helper retain their existing behavior.
"""

from __future__ import annotations

import glob
import os
import warnings
from typing import Optional

import pandas as pd

# The models train and predict on the raw measurement columns directly -- there
# are no engineered columns, so FEATURE_COLUMNS == MEASUREMENT_COLUMNS.
from . import thresholds as T

FEATURE_COLUMNS = list(T.MEASUREMENT_COLUMNS)

# Domain-typical fallback values (a "typical Fort Bend" soil) used only when a
# column is entirely missing from an input CSV.
DEFAULTS = {
    "ph": 7.0, "ec": 1.0,
    "nitrogen": 20.0, "phosphorus": 20.0, "potassium": 150.0,
}

# Physically plausible bounds; values outside are clipped (with a warning).
CLIP_BOUNDS = {
    "ph": (2.0, 11.0), "ec": (0.0, 40.0),
    "nitrogen": (0.0, 500.0), "phosphorus": (0.0, 1000.0), "potassium": (0.0, 2000.0),
}

# Forgiving column aliases so a farmer's/lab's CSV headers don't have to be exact.
COLUMN_ALIASES = {
    "ph": "ph", "soil_ph": "ph", "ph_1to1": "ph",
    "ec": "ec", "salinity": "ec", "ec_dsm": "ec", "conductivity": "ec",
    "n": "nitrogen", "nitrogen": "nitrogen", "nitrate": "nitrogen",
    "nitrate_n": "nitrogen", "n_ppm": "nitrogen",
    "p": "phosphorus", "phosphorus": "phosphorus", "p_ppm": "phosphorus", "phosphorus_ppm": "phosphorus",
    "k": "potassium", "potassium": "potassium", "k_ppm": "potassium", "potassium_ppm": "potassium",
    "lat": "latitude", "latitude": "latitude", "y": "latitude",
    "lon": "longitude", "lng": "longitude", "long": "longitude", "longitude": "longitude", "x": "longitude",
    "id": "sample_id", "sample": "sample_id", "sample_id": "sample_id", "point_id": "sample_id",
    "zone": "zone", "region": "region",
}


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lower-case, strip, and alias-map column names to the canonical schema."""
    renamed = {}
    for col in df.columns:
        key = str(col).strip().lower().replace(" ", "_").replace("%", "pct")
        renamed[col] = COLUMN_ALIASES.get(key, key)
    return df.rename(columns=renamed)


def validate_and_clean(df: pd.DataFrame, source: str = "input",
                       require_geo: bool = False) -> pd.DataFrame:
    """Standardize, coerce, impute, clip. Returns a clean copy; warns on repairs.

    require_geo=True (prediction) also guarantees sample_id / latitude / longitude.
    """
    df = standardize_columns(df.copy())

    # Coerce measurements to numeric; impute wholly-missing columns from defaults.
    for col in T.MEASUREMENT_COLUMNS:
        if col not in df.columns:
            warnings.warn("[%s] missing column '%s'; filled with default %.2f."
                          % (source, col, DEFAULTS[col]))
            df[col] = DEFAULTS[col]
        df[col] = pd.to_numeric(df[col], errors="coerce")
        n_missing = int(df[col].isna().sum())
        if n_missing:
            warnings.warn("[%s] %d missing '%s' value(s); imputed with default."
                          % (source, n_missing, col))
            df[col] = df[col].fillna(DEFAULTS[col])
        lo, hi = CLIP_BOUNDS[col]
        out_of_range = ((df[col] < lo) | (df[col] > hi))
        if out_of_range.any():
            warnings.warn("[%s] %d '%s' value(s) out of [%.1f, %.1f]; clipped."
                          % (source, int(out_of_range.sum()), col, lo, hi))
        df[col] = df[col].clip(lo, hi)
        # Keep numeric columns as float64 so train/predict feature matrices match.
        df[col] = df[col].astype("float64")

    # Identity / geo columns.
    if "region" not in df.columns:
        df["region"] = "fort_bend"
    df["region"] = df["region"].fillna("fort_bend")

    if require_geo:
        if "sample_id" not in df.columns:
            df["sample_id"] = pd.NA
        # Guarantee non-blank, UNIQUE sample_ids. Downstream maps key per-sample
        # zone labels and per-fix attribution by sample_id, so blank ('nan') or
        # duplicated labels (copied rows, reused lab point names) would otherwise
        # silently collapse and corrupt the report.
        ids, seen, auto, repaired = [], {}, 0, False
        for raw in df["sample_id"].tolist():
            sid = "" if (raw is None or (isinstance(raw, float) and pd.isna(raw))) else str(raw).strip()
            if sid == "" or sid.lower() == "nan":
                auto += 1
                sid = "AUTO-%03d" % auto
                repaired = True
            if sid in seen:
                seen[sid] += 1
                sid = "%s-dup%d" % (sid, seen[sid])
                repaired = True
            else:
                seen[sid] = 0
            ids.append(sid)
        if repaired:
            warnings.warn("[%s] blank/duplicate sample_id(s) were auto-filled "
                          "and made unique." % source)
        df["sample_id"] = ids
        for geo in ("latitude", "longitude"):
            if geo not in df.columns:
                raise ValueError(
                    "[%s] prediction input needs a '%s' column for zone mapping."
                    % (source, geo))
            df[geo] = pd.to_numeric(df[geo], errors="coerce")
        if df["latitude"].isna().any() or df["longitude"].isna().any():
            raise ValueError("[%s] some rows have missing latitude/longitude." % source)
        if "zone" not in df.columns:
            df["zone"] = pd.NA

    return df.reset_index(drop=True)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Raw measurements -> the exact feature matrix the models consume.

    The five soil measurements ARE the features (no engineered columns), returned
    as float64 in FEATURE_COLUMNS order.
    """
    return df[FEATURE_COLUMNS].astype("float64")


def load_farm_csv(path: str) -> pd.DataFrame:
    """Load + validate a single farm CSV for prediction (geo required)."""
    df = pd.read_csv(path)
    return validate_and_clean(df, source=os.path.basename(path), require_geo=True)


def load_real_farm_data(farm_dir: str = "data/farm_data") -> Optional[pd.DataFrame]:
    """Merge every real farm CSV under `data/farm_data/` for retraining.

    Returns None if the folder is empty/absent. This legacy helper imputes missing
    values. train.py uses its own complete-case loader for real/combined modes
    so missing public/farm nutrients never become synthetic defaults.
    """
    if not os.path.isdir(farm_dir):
        return None
    paths = sorted(glob.glob(os.path.join(farm_dir, "*.csv")))
    if not paths:
        return None
    frames = []
    for p in paths:
        try:
            frames.append(validate_and_clean(pd.read_csv(p),
                                              source=os.path.basename(p)))
        except Exception as exc:  # noqa: BLE001 - never let one bad file kill training
            warnings.warn("Skipping unreadable farm CSV %s: %s" % (p, exc))
    if not frames:
        return None
    merged = pd.concat(frames, ignore_index=True)
    print("  merged %d real farm sample(s) from %s" % (len(merged), farm_dir))
    return merged
