"""Multi-output soil models: health-score regressor, category classifier, and a
multi-label crop-suitability classifier.

Labels come from the agronomy oracle (deterministic expert rules) applied to the
training frame. The models learn feature -> label so that, once real farm data is
merged in, they generalize across noisy/partial inputs. Prescriptive fixes are
NOT modelled here -- those stay rule-based in the recommender, where precision
matters more than a model's approximation.

Gotchas handled explicitly (from a prior prototype):
  * XGBClassifier will not take raw string classes -> LabelEncoder, saved with
    the model, inverse-transformed at inference.
  * Crop suitability is multi-LABEL -> MultiOutputClassifier(RandomForestClassifier),
    never a regressor (a regressor has no .classes_ and breaks .fit()).
  * The score regressor is clamped to [0, 100] at inference -- stacked severe
    problems can push a raw regression prediction negative.
"""

from __future__ import annotations

import os
from typing import Dict, List

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.multioutput import MultiOutputClassifier
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier, XGBRegressor

from . import agronomy, evidence, preprocess, thresholds as T

SCORE_COL = "health_score"
CATEGORY_COL = "health_category"
CROP_COL_PREFIX = "crop_"


def attach_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Add oracle labels: health score, category, and one binary col per crop."""
    df = evidence.apply_crop_evidence(df)
    records = df.to_dict("records")
    df[SCORE_COL] = [agronomy.compute_health_score(r) for r in records]
    df[CATEGORY_COL] = [T.categorize(s) for s in df[SCORE_COL]]
    crop_matrix = np.array([agronomy.crop_viability_vector(r) for r in records])
    for i, crop in enumerate(T.CROP_NAMES):
        df[CROP_COL_PREFIX + crop] = crop_matrix[:, i]
    presence = df["signal_strength"].eq("presence_only").fillna(False)
    presence &= df["crop_signal_source"].eq("cdl").fillna(False)
    # Positive-only weak labels: CDL never provides negatives for absent crops.
    # Other targets retain their explicitly identified agronomy-oracle labels.
    for crop in T.CROP_NAMES:
        planted = presence & df["dominant_crop"].eq(crop).fillna(False)
        df.loc[planted, CROP_COL_PREFIX + crop] = 1
    # Future measured trial/yield imports must provide per-crop outcomes. Those
    # replace weak labels entirely; crop absence is not an observed failure.
    observed = df["crop_signal_source"].isin(["yield", "trial"])
    for crop in T.CROP_NAMES:
        column = "observed_crop_" + crop
        if column in df:
            values = pd.to_numeric(df[column], errors="coerce")
            use = observed & values.isin([0, 1])
            df.loc[use, CROP_COL_PREFIX + crop] = values.loc[use].astype(int)
    df["crop_label_source"] = "agronomy_oracle"
    df.loc[presence, "crop_label_source"] = "oracle_plus_cdl_presence"
    df.loc[observed, "crop_label_source"] = "oracle_plus_observed_outcomes"
    return df


def train_models(labeled_df: pd.DataFrame, seed: int = 42,
                 verbose: bool = True) -> Dict:
    """Train all three models and return a saveable bundle with metrics."""
    X = preprocess.build_features(labeled_df)
    # final_weight folds regional similarity into the crop-evidence weight.
    # Datasets built before region tiering carry only sample_weight.
    weight_column = "final_weight" if "final_weight" in labeled_df else "sample_weight"
    weights = pd.to_numeric(labeled_df.get(weight_column, pd.Series(1.0, index=labeled_df.index)),
                            errors="coerce").to_numpy()
    if not np.isfinite(weights).all() or (weights <= 0).any():
        raise ValueError("%s must be finite and positive" % weight_column)
    y_score = labeled_df[SCORE_COL].astype(float).values
    y_cat = labeled_df[CATEGORY_COL].astype(str).values
    crop_cols = [CROP_COL_PREFIX + c for c in T.CROP_NAMES]
    Y_crop = labeled_df[crop_cols].astype(int).values

    # Encode category labels for XGBoost (it rejects raw strings).
    cat_encoder = LabelEncoder().fit(y_cat)
    y_cat_enc = cat_encoder.transform(y_cat)

    idx_train, idx_test = train_test_split(
        np.arange(len(X)), test_size=0.2, random_state=seed)

    def sub(a, idx):
        return a.iloc[idx] if hasattr(a, "iloc") else a[idx]

    # --- health-score regressor -------------------------------------------
    score_model = XGBRegressor(
        n_estimators=400, max_depth=5, learning_rate=0.05,
        subsample=0.9, colsample_bytree=0.9, random_state=seed,
        objective="reg:squarederror", tree_method="hist", verbosity=0)
    score_model.fit(sub(X, idx_train), sub(y_score, idx_train), sample_weight=sub(weights, idx_train))
    score_pred = np.clip(score_model.predict(sub(X, idx_test)), 0, 100)
    score_metrics = {
        "r2": float(r2_score(sub(y_score, idx_test), score_pred)),
        "mae": float(mean_absolute_error(sub(y_score, idx_test), score_pred)),
    }

    # --- category classifier ----------------------------------------------
    category_model = XGBClassifier(
        n_estimators=400, max_depth=5, learning_rate=0.05,
        subsample=0.9, colsample_bytree=0.9, random_state=seed,
        tree_method="hist", verbosity=0, eval_metric="mlogloss")
    category_model.fit(sub(X, idx_train), sub(y_cat_enc, idx_train), sample_weight=sub(weights, idx_train))
    cat_pred = category_model.predict(sub(X, idx_test))
    category_metrics = {
        "accuracy": float(accuracy_score(sub(y_cat_enc, idx_test), cat_pred)),
        "classes": list(cat_encoder.classes_),
    }

    # --- crop suitability (multi-label) -----------------------------------
    crop_model = MultiOutputClassifier(
        RandomForestClassifier(
            n_estimators=250, max_depth=14, min_samples_leaf=2,
            random_state=seed, n_jobs=-1, class_weight="balanced_subsample"))
    crop_model.fit(sub(X, idx_train), sub(Y_crop, idx_train), sample_weight=sub(weights, idx_train))
    crop_pred = np.asarray(crop_model.predict(sub(X, idx_test)))
    Y_test = sub(Y_crop, idx_test)
    per_crop_acc = {
        crop: float(accuracy_score(Y_test[:, i], crop_pred[:, i]))
        for i, crop in enumerate(T.CROP_NAMES)
    }
    crop_metrics = {
        "mean_per_crop_accuracy": float(np.mean(list(per_crop_acc.values()))),
        "micro_f1": float(f1_score(Y_test, crop_pred, average="micro", zero_division=0)),
        "exact_match_ratio": float(np.mean(np.all(Y_test == crop_pred, axis=1))),
        "per_crop_accuracy": per_crop_acc,
    }

    bundle = {
        "score_model": score_model,
        "category_model": category_model,
        "category_encoder": cat_encoder,
        "crop_model": crop_model,
        "crop_names": list(T.CROP_NAMES),
        "feature_columns": list(preprocess.FEATURE_COLUMNS),
        "metadata": {
            "n_samples": int(len(X)),
            "sample_weight_column": weight_column,
            "sample_weight_counts": {str(w): int((weights == w).sum()) for w in np.unique(weights)},
            "crop_label_sources": labeled_df.get("crop_label_source", pd.Series(dtype=str)).value_counts().to_dict(),
            "validation_note": "Unweighted random row holdout; weak/oracle labels, not yield validation",
            "n_train": int(len(idx_train)),
            "n_test": int(len(idx_test)),
            "score_metrics": score_metrics,
            "category_metrics": category_metrics,
            "crop_metrics": crop_metrics,
        },
    }

    if verbose:
        print("  score regressor:   R2=%.3f  MAE=%.2f"
              % (score_metrics["r2"], score_metrics["mae"]))
        print("  category clf:      accuracy=%.3f  (classes: %s)"
              % (category_metrics["accuracy"], ", ".join(category_metrics["classes"])))
        print("  crop multi-label:  mean per-crop acc=%.3f  micro-F1=%.3f"
              % (crop_metrics["mean_per_crop_accuracy"], crop_metrics["micro_f1"]))

    return bundle


def save_bundle(bundle: Dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    joblib.dump(bundle, path)


def load_bundle(path: str) -> Dict:
    if not os.path.exists(path):
        raise FileNotFoundError(
            "No trained model at %s -- run `python train.py` first." % path)
    return joblib.load(path)


def _crop_viable_proba(crop_model, X) -> np.ndarray:
    """Prob of 'viable' per crop, robust to single-class outputs.

    MultiOutputClassifier.predict_proba returns one array per output; a crop
    that was always/never viable in training yields a single-class estimator.
    """
    n = len(X)
    probas = np.zeros((n, len(crop_model.estimators_)))
    try:
        proba_list = crop_model.predict_proba(X)
        for i, (est, pr) in enumerate(zip(crop_model.estimators_, proba_list)):
            classes = list(est.classes_)
            if 1 in classes:
                probas[:, i] = pr[:, classes.index(1)]
            else:  # only class 0 was ever seen -> viable prob is 0
                probas[:, i] = 0.0
    except Exception:  # noqa: BLE001 - fall back to hard predictions
        probas = np.asarray(crop_model.predict(X), dtype=float)
    return probas


def predict_frame(bundle: Dict, features: pd.DataFrame) -> List[Dict]:
    """Run all models on a feature frame; return one result dict per row."""
    X = features[bundle["feature_columns"]].astype(float)

    scores = np.clip(bundle["score_model"].predict(X), 0, 100)
    cat_enc = bundle["category_model"].predict(X)
    cats = bundle["category_encoder"].inverse_transform(cat_enc)
    crop_pred = np.asarray(bundle["crop_model"].predict(X))
    crop_proba = _crop_viable_proba(bundle["crop_model"], X)
    crop_names = bundle["crop_names"]

    results = []
    for i in range(len(X)):
        results.append({
            "ml_health_score": round(float(scores[i]), 1),
            # Headline category is banded from the (very accurate) score so score
            # and category never disagree; the standalone classifier's raw call
            # is kept alongside for transparency.
            "ml_category_from_score": T.categorize(float(scores[i])),
            "ml_health_category": str(cats[i]),
            "ml_crop_suitable": [
                crop_names[j] for j in range(len(crop_names)) if crop_pred[i, j] == 1
            ],
            "ml_crop_confidence": {
                crop_names[j]: round(float(crop_proba[i, j]), 3)
                for j in range(len(crop_names))
            },
        })
    return results
