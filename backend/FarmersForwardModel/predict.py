#!/usr/bin/env python3
"""Run the FarmersFirst pipeline on a farm CSV and produce all four deliverables.

    python predict.py --input data/sample_field.csv

Outputs (into outputs/):
    <field>_report.json    structured report for the future app
    <field>_report.txt     plain-English report for on-site reading
    <field>_zones.csv      per-zone summary table
    <field>_field_map.png  color-coded soil-health field map

Design split:
  * ML models -> holistic health score + category + crop-suitability signal.
  * Rule-based agronomy -> problem detection, specific costed fixes, and the
    authoritative "what can I plant right now" crop check (precision matters).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from collections import defaultdict
from typing import Dict, List

import pandas as pd

from pipeline import agronomy, model as M, preprocess, recommender, reporter
from pipeline import thresholds as T
from pipeline import zone_mapper

DEFAULT_MODEL_PATH = os.path.join("models", "soil_models.joblib")


# ---------------------------------------------------------------------------
# per-sample analysis
# ---------------------------------------------------------------------------
def analyze_sample(measurements: Dict, ml_result: Dict, ids: Dict) -> Dict:
    """Combine ML output with rule-based agronomy for one sample."""
    problems = agronomy.detect_problems(measurements)
    primary = agronomy.primary_problem(measurements)
    rule_score = agronomy.compute_health_score(measurements)
    viability = agronomy.crop_viability(measurements)
    recs = recommender.build_recommendations(measurements, problems)
    cost_benefit = recommender.estimate_cost_benefit(measurements, recs)

    return {
        "sample_id": ids["sample_id"],
        "latitude": ids["latitude"],
        "longitude": ids["longitude"],
        "zone": ids["zone"],
        "measurements": {c: round(float(measurements[c]), 2)
                         for c in T.MEASUREMENT_COLUMNS},
        # Headline score/category come from the ML model (the product's brain);
        # the rule score is kept for transparency and is usually very close.
        "health_score": ml_result["ml_health_score"],
        "health_category": ml_result["ml_category_from_score"],
        "ml_health_score": ml_result["ml_health_score"],
        "ml_category_classifier": ml_result["ml_health_category"],
        "rule_health_score": round(rule_score, 1),
        "primary_problem": {
            "code": primary["code"],
            "label": reporter.problem_label(primary["code"]),
            "severity": primary["severity"],
            "message": primary["message"],
        },
        "problems": problems,
        "recommendations": recs,
        "crop_suitability": {
            "viable_now": viability["viable"],
            "not_viable": viability["not_viable"],
        },
        "ml_crop_suitable": ml_result["ml_crop_suitable"],
        "ml_crop_confidence": ml_result["ml_crop_confidence"],
        "cost_benefit_experimental": cost_benefit,
        # internal helpers (not serialized directly)
        "_problems": problems,
        "_viable": viability["viable"],
    }


# ---------------------------------------------------------------------------
# field-level aggregation
# ---------------------------------------------------------------------------
def aggregate_field_recommendations(samples: List[Dict],
                                    raw_by_id: Dict[str, Dict]) -> List[Dict]:
    """Field-wide prioritized fixes: for each problem type, use the worst-hit
    sample to size the fix, and report how many zones it affects."""
    best: Dict[str, Dict] = {}
    for s in samples:
        for p in s["_problems"]:
            fc = p.get("fix_code")
            if fc is None:
                continue
            e = best.setdefault(fc, {"penalty": -1.0, "measurements": None,
                                     "problem": None, "samples": set(), "zones": set()})
            e["samples"].add(s["sample_id"])
            e["zones"].add(s["zone"])
            if p["penalty"] > e["penalty"]:
                e["penalty"] = p["penalty"]
                e["measurements"] = raw_by_id[s["sample_id"]]
                e["problem"] = p

    field_recs = []
    for fc, e in best.items():
        rec_list = recommender.build_recommendations(e["measurements"], [e["problem"]])
        if not rec_list:
            continue
        rec = rec_list[0]
        rec["affected_samples"] = len(e["samples"])
        rec["affected_zones"] = len(e["zones"])
        field_recs.append(rec)
    field_recs.sort(key=lambda r: r["priority_weight"], reverse=True)
    return field_recs


def field_crop_suitability(zone_summary: pd.DataFrame) -> Dict:
    """Field-level crop viability, computed from each zone's *average* conditions
    so it stays consistent with the zone-by-zone table (which uses zone means)."""
    n_zones = len(zone_summary)
    count = defaultdict(int)
    for _, z in zone_summary.iterrows():
        means = {c: float(z["mean_" + c]) for c in T.MEASUREMENT_COLUMNS}
        for crop in agronomy.crop_viability(means)["viable"]:
            count[crop] += 1
    crops = list(T.CROP_DEFAULT_YIELD)  # the crops crop_viability judges
    everywhere = [c for c in crops if count[c] == n_zones and n_zones > 0]
    nowhere = [c for c in crops if count[c] == 0]
    some = [{"crop": c, "n_zones": count[c]}
            for c in crops if 0 < count[c] < n_zones]
    return {"viable_everywhere": everywhere,
            "viable_in_some_zones": some,
            "not_viable_anywhere": nowhere}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Run FarmersFirst on a farm CSV.")
    ap.add_argument("--input", required=True, help="path to a farm sample CSV")
    ap.add_argument("--model", default=DEFAULT_MODEL_PATH)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--field-name", default=None,
                    help="label for the field (defaults to the CSV filename)")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.input))[0]
    field_name = args.field_name or stem.replace("_", " ").title()

    print("Loading model bundle: %s" % args.model)
    bundle = M.load_bundle(args.model)

    print("Loading + validating: %s" % args.input)
    df = preprocess.load_farm_csv(args.input)
    print("  %d samples loaded." % len(df))

    # ML predictions for every sample.
    features = preprocess.build_features(df)
    ml_results = M.predict_frame(bundle, features)

    # Per-sample analysis (ML + rules).
    raw_records = df.to_dict("records")
    samples: List[Dict] = []
    raw_by_id: Dict[str, Dict] = {}
    for i, row in enumerate(raw_records):
        ids = {
            "sample_id": str(row.get("sample_id", "S%03d" % (i + 1))),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "zone": row.get("zone") if pd.notna(row.get("zone")) else None,
        }
        raw_by_id[ids["sample_id"]] = row
        samples.append(analyze_sample(row, ml_results[i], ids))

    # Build the per-sample frame the zone mapper needs.
    zdf = df.copy()
    zdf["sample_id"] = [s["sample_id"] for s in samples]
    zdf["health_score"] = [s["health_score"] for s in samples]
    zdf["primary_problem_code"] = [s["primary_problem"]["code"] for s in samples]

    zdf, grid_meta = zone_mapper.assign_zones(zdf)
    # propagate the (possibly newly assigned) zone back onto each sample
    zone_by_id = dict(zip(zdf["sample_id"], zdf["zone"]))
    for s in samples:
        s["zone"] = zone_by_id.get(s["sample_id"], s["zone"])

    zone_summary = zone_mapper.aggregate_zones(zdf)

    map_path = os.path.join(args.outdir, "%s_field_map.png" % stem)
    zone_mapper.render_field_map(zdf, zone_summary, grid_meta, map_path,
                                 field_name=field_name)

    # Field-level rollups.
    scores = [s["health_score"] for s in samples]
    mean_score = sum(scores) / len(scores)
    soil_profile = {
        c: round(sum(s["measurements"][c] for s in samples) / len(samples), 2)
        for c in T.MEASUREMENT_COLUMNS
    }
    prob_codes = [s["primary_problem"]["code"] for s in samples
                  if s["primary_problem"]["code"] != "none"]
    dominant = max(set(prob_codes), key=prob_codes.count) if prob_codes else "none"
    field_recs = aggregate_field_recommendations(samples, raw_by_id)
    field_cb = recommender.estimate_cost_benefit(
        raw_by_id[samples[0]["sample_id"]], field_recs)

    field = {
        "field_name": field_name,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_samples": len(samples),
        "n_zones": int(zone_summary["zone"].nunique()),
        "field_summary": {
            "mean_health_score": round(mean_score, 1),
            "health_category": T.categorize(mean_score),
            "min_health_score": round(min(scores), 1),
            "max_health_score": round(max(scores), 1),
            "dominant_problem_code": dominant,
            "dominant_problem_label": reporter.problem_label(dominant),
            "n_samples_with_problems": len(prob_codes),
        },
        "field_soil_profile": soil_profile,
        "field_recommendations": field_recs,
        "field_cost_benefit": field_cb,
        "field_crop_suitability": field_crop_suitability(zone_summary),
        "zones": zone_summary.to_dict("records"),
        "samples": [{k: v for k, v in s.items() if not k.startswith("_")}
                    for s in samples],
        "map_path": map_path,
    }

    # Write deliverables.
    json_path = os.path.join(args.outdir, "%s_report.json" % stem)
    txt_path = os.path.join(args.outdir, "%s_report.txt" % stem)
    zones_csv = os.path.join(args.outdir, "%s_zones.csv" % stem)
    reporter.write_json_report(field, json_path)
    reporter.write_text_report(field, txt_path)
    zone_summary.to_csv(zones_csv, index=False)

    print("-" * 70)
    print("Field: %s" % field_name)
    print("Overall soil health: %.0f/100 (%s)"
          % (field["field_summary"]["mean_health_score"],
             field["field_summary"]["health_category"]))
    print("Top problem: %s" % field["field_summary"]["dominant_problem_label"])
    print("Wrote:")
    for p in (json_path, txt_path, zones_csv, map_path):
        print("  %s" % p)


if __name__ == "__main__":
    main()
