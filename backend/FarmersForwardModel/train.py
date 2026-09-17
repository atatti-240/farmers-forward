#!/usr/bin/env python3
"""Train the FarmersFirst soil-health models end to end.

    python train.py                     # synthetic baseline
    python train.py --data-source real --out models/real.joblib
    python train.py --data-source combined --out models/combined.joblib
    python train.py --data-source combined --region fort_bend_only   # narrow comparison
    python train.py --n-samples 8000    # more synthetic data
    python train.py --seed 7            # different synthetic draw

Generates a realistic synthetic training set for the Fort Bend County / Brazos
region across the five measured soil properties (pH, EC, N, P, K), optionally
loads complete real rows, attaches oracle labels, trains the three models, and saves a
single model bundle plus a metadata JSON.

The soil model measures exactly five properties:

    ph, ec, nitrogen, phosphorus, potassium

Geographic columns (latitude, longitude, region) are generated for realism and
carried through, but only the five measurements are used as model features.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import agronomy
from pipeline import model as M
from pipeline import preprocess

DEFAULT_MODEL_PATH = os.path.join("models", "soil_models.joblib")
MODEL_ROOT = Path(__file__).resolve().parent
DEFAULT_REAL_DATA = MODEL_ROOT / "data" / "real" / "fort_bend_real_training.csv"
EXPANDED_REAL_DATA = MODEL_ROOT / "data" / "real" / "expanded_real_training.csv"
# fort_bend_only is the original narrow dataset, kept so the two can be compared
# directly. expanded adds the same-MLRA and wider Gulf tiers, each carrying the
# region_weight that scales its influence on the fit.
REGION_DATASETS = {"fort_bend_only": DEFAULT_REAL_DATA, "expanded": EXPANDED_REAL_DATA}

# Fort Bend County, TX realistic sampling ranges for the five measurements.
# pH is drawn skewed toward 6.5-7.5 (see _draw_ph); the rest are bounded here.
FORT_BEND_RANGES = {
    "ph": (5.5, 8.5),
    "ec": (0.1, 6.0),          # dS/m
    "nitrogen": (5.0, 80.0),   # ppm
    "phosphorus": (5.0, 60.0), # ppm
    "potassium": (50.0, 350.0),# ppm
}

# Fort Bend County bounding box (approx), used only to stamp realistic geo.
_LAT_RANGE = (29.35, 29.85)
_LON_RANGE = (-96.05, -95.55)


# Probability each individual property is drawn from its "problem" sub-range
# rather than its healthy sub-range. Drawing the five independently means a
# sample can carry anywhere from 0 to 5 simultaneous problems, so the training
# set spans the full score spectrum (Excellent -> Poor) instead of piling up at
# Excellent. Healthy sub-ranges are chosen to sit fully inside the no-penalty
# zone, so an all-healthy draw scores 100 and upholds the label contract.
_P_PROBLEM = 0.40


def _draw_property(rng: np.random.Generator, good, problem) -> float:
    """Draw from the healthy range most of the time, the problem range otherwise.

    `good` and `problem` are (low, high) tuples; `problem` may be a list of
    tuples (e.g. acidic AND alkaline sub-ranges), one picked at random.
    """
    if rng.random() < _P_PROBLEM:
        if isinstance(problem, list):
            problem = problem[int(rng.integers(len(problem)))]
        return float(rng.uniform(*problem))
    return float(rng.uniform(*good))


def synthesize_samples(n_samples: int, rng: np.random.Generator) -> pd.DataFrame:
    """Generate `n_samples` realistic Fort Bend soil samples.

    Each of the five properties is drawn independently from a healthy sub-range
    or (with probability _P_PROBLEM) a problem sub-range, so soils range from
    clearly healthy to multi-problem. Only the five measurements are drawn -- plus
    geo/region metadata that is carried through but never used as a feature.
    """
    rows = []
    for _ in range(n_samples):
        s = {
            # pH: healthy 6.1-7.0 (zero penalty, skewed toward the 6.5 centre);
            # problem = acidic 5.5-6.0 or alkaline 7.0-8.5.
            "ph": _draw_property(rng, (6.1, 7.0), [(5.5, 6.0), (7.0, 8.5)]),
            # EC: healthy 0.1-2.0 dS/m; problem 2.0-6.0 (salinity).
            "ec": _draw_property(rng, (0.1, 2.0), (2.0, 6.0)),
            # N: healthy 20-80 ppm; problem 5-20 (deficient).
            "nitrogen": _draw_property(rng, (20.0, 80.0), (5.0, 20.0)),
            # P: healthy 15-60 ppm; problem 5-15 (deficient).
            "phosphorus": _draw_property(rng, (15.0, 60.0), (5.0, 15.0)),
            # K: healthy 100-350 ppm; problem 50-100 (deficient).
            "potassium": _draw_property(rng, (100.0, 350.0), (50.0, 100.0)),
            # geo/region metadata (carried through, not used as a model feature)
            "latitude": float(rng.uniform(*_LAT_RANGE)),
            "longitude": float(rng.uniform(*_LON_RANGE)),
            "region": "fort_bend",
            "source": "synthetic",
        }
        rows.append(s)
    return pd.DataFrame(rows)


def _verify_label_contract() -> None:
    """Sanity-check the oracle's health-score contract before training.

    Contract: pH 6.0-7.0 AND EC < 2.0 AND N > 20 AND P > 15 AND K > 100 -> 80+.
    """
    canonical = [
        {"ph": 6.5, "ec": 1.0, "nitrogen": 40, "phosphorus": 25, "potassium": 180},
        {"ph": 6.0, "ec": 0.3, "nitrogen": 21, "phosphorus": 16, "potassium": 101},
        {"ph": 7.0, "ec": 1.9, "nitrogen": 75, "phosphorus": 55, "potassium": 320},
    ]
    for c in canonical:
        score = agronomy.compute_health_score(c)
        assert score >= 80.0, (
            "label contract violated: %r scored %.1f (< 80)" % (c, score))
    print("  label contract OK: healthy soils (pH 6-7, EC<2, N>20, P>15, K>100) "
          "score 80+")


def load_training_data(data_source, n_samples, seed, real_path, farm_dir):
    """Select sources without imputing missing real nutrients or clipping them.

    Partial public records remain in the ingestion CSV for future use. The
    five-input oracle cannot label them honestly, so only complete, in-range
    real records reach it. Synthetic mode never reads real or farm CSVs.
    """
    frames = []
    counts = {}
    if data_source in ("synthetic", "combined"):
        frames.append(synthesize_samples(n_samples, np.random.default_rng(seed)))
        counts["synthetic"] = {"available": n_samples, "used": n_samples, "excluded": 0}
    if data_source in ("real", "combined"):
        if not Path(real_path).is_file():
            raise ValueError("Real dataset not found: %s. Run data/build_real_dataset.py first."
                             % real_path)
        public = pd.read_csv(real_path, low_memory=False, keep_default_na=False, na_values=[""])
        if "source" not in public:
            public["source"] = "real_public"
        raw_frames = [public]
        for path in sorted(Path(farm_dir).glob("*.csv")):
            farm = preprocess.standardize_columns(pd.read_csv(path))
            farm["source"] = "real_farm"
            raw_frames.append(farm)
        real = pd.concat(raw_frames, ignore_index=True)
        real["source"] = real["source"].fillna("real_public")
        if real["source"].eq("synthetic").any():
            raise ValueError("The real dataset contains rows marked synthetic.")
        valid = pd.Series(True, index=real.index)
        for column in preprocess.FEATURE_COLUMNS:
            real[column] = pd.to_numeric(real.get(column, pd.Series(index=real.index, dtype=float)),
                                         errors="coerce")
            low, high = preprocess.CLIP_BOUNDS[column]
            valid &= real[column].between(low, high)
        for source, rows in real.groupby("source", sort=True):
            used = int(valid.loc[rows.index].sum())
            counts[str(source)] = {"available": len(rows), "used": used,
                                   "excluded": len(rows) - used}
        eligible = real.loc[valid].copy()
        if eligible.empty:
            message = ("No complete, in-range real rows with all five inputs. "
                       "Missing nutrients are not imputed. Counts: %s" % counts)
            if data_source == "real":
                raise ValueError(message)
            warnings.warn(message + ". Combined run will contain synthetic rows only.")
        else:
            frames.append(eligible)
    if not frames:
        raise ValueError("No training data selected.")
    selected = pd.concat(frames, ignore_index=True)
    if len(selected) < 5:
        raise ValueError("At least five complete rows are required for the train/test split.")
    return selected, counts


def main() -> None:
    ap = argparse.ArgumentParser(description="Train FarmersFirst soil models.")
    ap.add_argument("--n-samples", type=int, default=6000,
                    help="number of synthetic samples to generate (default 6000)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--data-source", choices=("synthetic", "real", "combined"),
                    default="synthetic", help="training input source (default: synthetic)")
    ap.add_argument("--region", choices=tuple(REGION_DATASETS), default="expanded",
                    help="which standardized dataset real/combined modes read (default: expanded)")
    ap.add_argument("--real-data", type=Path, default=None,
                    help="standardized public-data CSV; overrides --region when given")
    ap.add_argument("--out", default=DEFAULT_MODEL_PATH,
                    help="output model bundle path")
    ap.add_argument("--farm-data", type=Path, default=MODEL_ROOT / "data" / "farm_data",
                    help="additional farm CSVs for real/combined modes only")
    args = ap.parse_args()
    if args.real_data is None:
        args.real_data = REGION_DATASETS[args.region]

    if args.data_source != "real" and args.n_samples < 5000:
        warnings.warn("n_samples=%d is below the recommended minimum of 5000; "
                      "bumping to 5000." % args.n_samples)
        args.n_samples = 5000

    print("=" * 70)
    print("FarmersFirst -- training soil-health models")
    print("=" * 70)

    print("[1/5] Selecting %s training data ..." % args.data_source)
    _verify_label_contract()
    try:
        df, source_counts = load_training_data(
            args.data_source, args.n_samples, args.seed, args.real_data, args.farm_data)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))
    print("[2/5] Source counts (available / used / excluded):")
    for source, counts in source_counts.items():
        print("  %s: %d / %d / %d" % (source, counts["available"], counts["used"],
                                        counts["excluded"]))

    print("[3/5] Validating + cleaning %d samples ..." % len(df))
    # Raw provenance includes aliases such as nitrate_n. Only normalize the
    # canonical features here, so those fields cannot become duplicate
    # nitrogen columns or overwrite the selected measured/derived value.
    columns = preprocess.FEATURE_COLUMNS
    # load_training_data already dropped every incomplete real row, so
    # preprocess.DEFAULTS must not fire here. Assert that instead of trusting
    # the suppressed warnings below: a silent default would erase the
    # measured/derived/null provenance that ingestion recorded and turn a
    # missing lab value into a fabricated "typical Fort Bend" measurement.
    blank = df[columns].isna().sum()
    if blank.any():
        raise ValueError(
            "Incomplete rows reached cleaning; defaults would overwrite missing "
            "real measurements: %s" % {k: int(v) for k, v in blank.items() if v})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # synthetic data is already in-range
        cleaned = preprocess.validate_and_clean(df[columns], source="training", require_geo=False)
        df[columns] = cleaned[columns]

    print("[4/5] Attaching oracle labels ...")
    labeled = M.attach_labels(df)
    cat_counts = labeled[M.CATEGORY_COL].value_counts().to_dict()
    print("  health-category balance: %s"
          % ", ".join("%s=%d" % (k, v) for k, v in cat_counts.items()))

    print("[5/5] Training models ...")
    bundle = M.train_models(labeled, seed=args.seed)

    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    bundle["metadata"]["trained_at"] = generated_at
    bundle["metadata"]["data_source"] = args.data_source
    bundle["metadata"]["region"] = args.region
    bundle["metadata"]["real_data_path"] = str(args.real_data)
    bundle["metadata"]["source_counts"] = source_counts
    bundle["metadata"]["n_synthetic_samples"] = int(df["source"].eq("synthetic").sum())
    bundle["metadata"]["n_real_samples"] = int(df["source"].ne("synthetic").sum())
    bundle["metadata"]["n_real_farm_samples"] = int(df["source"].eq("real_farm").sum())
    bundle["metadata"]["label_source"] = "agronomy_oracle_with_positive_only_CDL_and_explicit_observed_overrides"
    bundle["metadata"]["real_missing_policy"] = "exclude_incomplete_or_out_of_range"

    M.save_bundle(bundle, args.out)
    meta_path = os.path.splitext(args.out)[0] + "_metadata.json"
    os.makedirs(os.path.dirname(os.path.abspath(meta_path)), exist_ok=True)
    with open(meta_path, "w") as fh:
        json.dump({
            "trained_at": generated_at,
            "feature_columns": bundle["feature_columns"],
            "crop_names": bundle["crop_names"],
            "metadata": bundle["metadata"],
        }, fh, indent=2)

    print("-" * 70)
    print("Saved model bundle -> %s" % args.out)
    print("Saved metadata     -> %s" % meta_path)
    print("Done. Next:  python predict.py --input data/sample_field.csv")


if __name__ == "__main__":
    main()
