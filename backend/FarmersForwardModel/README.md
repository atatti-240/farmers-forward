# FarmersFirst - Soil Health ML Pipeline

A modular Python pipeline that turns soil measurements into an **actionable soil
health assessment** for small-scale Texas farmers: a 0–100 health score, the
primary problem, **specific costed fixes** (rate + cost + timing), which crops
are viable right now, a color-coded GPS field map, and dual JSON + plain-English
reports.

Built for [FarmersFirst](#about) - free, EC-based soil testing for growers
across Fort Bend County and the Brazos River region.

The model measures exactly **five soil properties**: pH, EC (salinity), and the
three primary macronutrients **N / P / K**.

---

## Quickstart

```bash
# 1. Install dependencies (Python 3.9+)
pip install -r requirements.txt

# 2. Train all models (synthetic Fort Bend baseline)
python train.py

# 3. Run the pipeline on the included sample field
python predict.py --input data/sample_field.csv
```

Step 3 writes four deliverables into `outputs/`:

| File | What it is |
|---|---|
| `sample_field_report.json` | Structured report for the future app |
| `sample_field_report.txt` | Plain-English report to read on-site |
| `sample_field_zones.csv` | Per-zone summary table |
| `sample_field_field_map.png` | Color-coded soil-health field map |

Training is **fully offline** - it synthesizes a realistic Fort Bend training set
across the five measured properties, so `python train.py` never depends on the
network.

---

## What you get

Running `predict.py` produces, per field and per sample:

1. **Soil health score** - 0–100, clamped.
2. **Health category** - Critical / Poor / Fair / Good / Excellent.
3. **Primary problem** - the single most severe issue detected.
4. **Specific fixes** - e.g. *"Apply 2.5–3.0 tons/acre agricultural lime,
   incorporate 6 in deep, 3–6 months before planting, ~$63–100/acre."* Every fix
   carries a **rate, cost estimate, and timing window.**
5. **Crop suitability** - which of 10 crops are viable *right now*.
6. **GPS zone mapping** - samples are binned into management zones (farmer-labeled
   or an automatic lat/lon grid) and rendered as a red→green field map.
7. **Dual-format report** - machine-readable JSON + farmer-readable text.
8. **[EXPERIMENTAL] cost/benefit** - a clearly-labeled ballpark of what the fixes
   cost vs. protect. *Not financial advice.*

---

## How it works

```
              ┌─────────────────────────────────────────────────────┐
 farm CSV ──▶ │ preprocess: validate, clean, canonicalize columns    │
              └─────────────────────────────────────────────────────┘
                        │                              │
                        ▼                              ▼
        ┌───────────────────────────┐   ┌───────────────────────────────┐
        │ ML models (learned)       │   │ agronomy rules (deterministic) │
        │ • health-score regressor  │   │ • problem detection            │
        │ • category classifier     │   │ • crop viability "now"         │
        │ • crop multi-label clf     │   │ • → recommender: costed fixes  │
        └───────────────────────────┘   └───────────────────────────────┘
                        │                              │
                        └──────────────┬───────────────┘
                                       ▼
              ┌─────────────────────────────────────────────────────┐
              │ zone_mapper (grid + field map)  →  reporter (JSON/txt)│
              └─────────────────────────────────────────────────────┘
```

The five measurements (pH, EC, N, P, K) are the model features directly - there
is no feature engineering to drift between train and predict.

**Why both ML and rules?** The two play to their strengths:

- **ML models** give the holistic *score / category / crop-suitability signal*
  and generalize once real, noisy farm data is merged in.
- **Rule-based agronomy** drives *problem detection and the specific fixes*,
  where precision matters far more than a model's approximation - a farmer needs
  the right lime rate, not a regression's guess. The same rules act as the
  **labeling oracle** that generates ground-truth labels for the synthetic
  training data.

The two agree closely (on the sample field the ML score tracks the rule score
within a couple of points), and the report shows both for transparency.

### Project layout

```
farmersfirst/
├── data/
│   ├── sample_field.csv       # 16-sample Fort Bend field (testable immediately)
│   ├── test_field.csv         # tiny 3-sample field for a quick smoke test
│   └── farm_data/             # ← drop real farm CSVs here to retrain (see below)
├── pipeline/
│   ├── thresholds.py          # single source of truth: all TX thresholds + crops
│   ├── agronomy.py            # health score, problem detection, crop viability
│   ├── preprocess.py          # cleaning, validation, column contract
│   ├── recommender.py         # rule-based costed fixes + experimental cost/benefit
│   ├── model.py               # multi-output ML models (train/save/load/predict)
│   ├── zone_mapper.py         # GPS grid aggregation + field-map rendering
│   └── reporter.py            # JSON + plain-English report builders
├── models/                    # saved model bundle (created by train.py)
├── outputs/                   # generated reports/maps (created by predict.py)
├── train.py                   # end-to-end training (synthetic data generator)
├── predict.py                 # inference entry point
├── requirements.txt
└── README.md
```

---

## Adding real farm data (drop-in retraining)

No code changes needed:

1. Drop one or more farm CSVs into `data/farm_data/`.
2. Run `python data/build_real_dataset.py` once to fetch public soil data and crop context (install requirements-ingestion.txt first).
3. Re-run `python train.py --data-source combined --out models/combined_experiment.joblib`.

Complete, in-range rows are merged into the training set.
Headers are forgiving - `pH`, `EC`, `N`, `P`, `K`, `nitrate_n`, `lat`, `lon`,
etc. are all recognized and aliased to the canonical schema. In real/combined
training, incomplete and out-of-range real rows are excluded, with counts reported.
Prediction retains its existing imputation behavior.

For five-source ingestion, method units, nitrogen-credit parameters, crop weights, and coverage, see
[data/real/README.md](data/real/README.md).

### Input CSV schema

The pipeline reads exactly these columns (measurements are imputed with a warning
if missing; the geo columns are **required for prediction** so samples can be
mapped to zones):

| Column | Unit / notes |
|---|---|
| `sample_id` | unique per sample (auto-filled if blank) |
| `latitude` | decimal degrees (required for prediction) |
| `longitude` | decimal degrees (required for prediction) |
| `region` | optional (defaults to `fort_bend`) |
| `zone` | optional (else auto-gridded from lat/lon) |
| `ph` | - |
| `ec` | dS/m (salinity) |
| `nitrogen` | ppm |
| `phosphorus` | ppm |
| `potassium` | ppm |

---

## Texas soil thresholds

These are FarmersFirst's Texas-specific thresholds (not generic textbook
numbers), defined once in `pipeline/thresholds.py`:

| Metric | Thresholds |
|---|---|
| pH | alert <5.5 or >7.8; **optimal 6.0–7.0** |
| EC (salinity) | >2 dS/m stress, >4 severe, >8 lethal |
| Nitrogen | <20 ppm deficient |
| Phosphorus | <15 ppm deficient, >150 ppm runoff risk |
| Potassium | <100 ppm deficient |

> **Brazos note:** irrigation water in the Fort Bend / Richmond reach runs
> saltier downstream - the salinity fix flags this explicitly.

The health score starts at 100 and subtracts a penalty for pH distance from the
6.0–7.0 optimum, high salinity, and N/P/K deficiencies/excesses. A soil with
**pH 6.0–7.0 AND EC < 2.0 AND N > 20 AND P > 15 AND K > 100** incurs no penalty
and scores **80+ (Excellent)**.

Crop viability thresholds (pH range, EC max, N/P/K minimums) for Corn, Cotton,
Sorghum, Soybeans, Wheat, Tomatoes, Peppers, Blueberries, Strawberries, and Hemp
are likewise in `thresholds.py`.

---

## Data sources

- **Synthetic:** `train.py` generates realistic Fort Bend County samples across
  the five measured properties, using local sampling ranges:

  | Property | Range | Notes |
  |---|---|---|
  | pH | 5.5–8.5 | skewed toward 6.5–7.5 |
  | EC | 0.1–6.0 dS/m | |
  | Nitrogen | 5–80 ppm | |
  | Phosphorus | 5–60 ppm | |
  | Potassium | 50–350 ppm | |

  A fraction of samples get an injected stressor (extreme pH, salinity spike, or
  an N/P/K shortage) and a fraction are forced clearly healthy, so the models see
  well-labeled examples of every category.
- **Real:** SSURGO, KSSL, SoilGrids, CDL, and WSS-backed exports live in `data/real/`. Use
  `--data-source real` or `--data-source combined`; both also read `data/farm_data/`.
  Partial records are preserved in the exports and excluded from training.

### What the public data can and cannot supply today

The five public sources cover 186,706 Fort Bend soil records, but **only 2 of
them carry all five model inputs**, and both are KSSL layers from the same pedon.
Phosphorus is the binding constraint, not a join bug:

| Input | Best public coverage | Why |
| --- | --- | --- |
| pH | 147,029 rows | SoilGrids prediction, SSURGO representative value, KSSL lab |
| EC | 355 rows | KSSL saturation extract and SSURGO only; SoilGrids has no EC layer |
| Nitrogen | 147,025 rows, all OM-derived | see below |
| **Phosphorus** | **2 rows** | SSURGO `pbray1_r` is empty for all 333 TX157 horizons; KSSL holds 2 Olsen-P values in the whole county; SoilGrids has no extractable-P layer |
| Potassium | 304 rows | KSSL NH4OAc pH 7 only; CEC is never converted into K |

**Nitrogen is 0 measured / 147,025 OM-derived / 39,681 null.** The zero is a
property of the sources, not a broken join, for two independent reasons:

1. KSSL `nitrate_1m_kcl` is unpopulated for every one of the 311 Fort Bend layers,
   so there is no measured nitrate-N in the region's lab holdings.
2. SoilGrids does publish a nitrogen layer, but it predicts **total N** (median
   2,620 ppm here), which is mostly organic and not plant available. The model
   feature is NO3-N in ppm, flagged deficient below 20 and clipped at 500, so
   feeding total N into it would pin all 146,514 grid rows at the clip ceiling.
   It is retained as `soilgrids_total_nitrogen_ppm` for reference and never
   becomes the `nitrogen` feature. `summary.json` records both reasons.

Every nitrogen value therefore comes from the `OM% * 15` mineralization-credit
proxy, flagged `n_derived = True` and `n_source = om_derived`. That is a modeling
assumption, not a lab result.

### Widening the region: MLRA 150A and the Gulf Coast tier

Because Fort Bend alone yields 2 usable rows, ingestion also pulls two wider
tiers. Neither is a hand-picked list of nearby states. Fort Bend's Major Land
Resource Area is derived from SDA (`legend` joined to `laoverlap`) and comes back
as **MLRA 150A, Gulf Coast Prairies**, shared by **40 soil survey areas**. A
wider Gulf Coast box (28 to 33 N, 98 to 82 W) adds a lower-confidence third tier.

| region_tier | Rows | Complete five-input rows | region_weight |
| --- | ---: | ---: | ---: |
| `fort_bend` | 186,577 | 2 | 1.0 |
| `same_mlra` | 18,529 | 114 | 0.7 |
| `gulf_coast_regional` | 19,208 | 191 | 0.4 |
| **Total** | **224,314** | **307** | |

Training-eligible rows go from **2 to 307**, phosphorus coverage from 2 rows to
1,506, and potassium from 182 to 15,636. Each row's influence is scaled by
`final_weight = region_weight * evidence_weight`, so the wider tier adds volume
without being treated as equivalent to local data. The three weights are
constants at the top of `data/build_real_dataset.py`.

`--region expanded` (the default) trains on all three tiers;
`--region fort_bend_only` keeps the original narrow behavior for comparison.
In a combined run the expanded set is the only source of `Poor` and `Critical`
health examples, which the synthetic generator and the Fort Bend rows never
produce.

The practical consequence: `--data-source real` still exits under
`--region fort_bend_only` (2 rows is below the five-row split minimum) but
succeeds under `--region expanded`. Measured nitrate-N is still 0 across all
18,359 Gulf-region KSSL layers, and 307 real rows against 6,000 synthetic ones
remains a small share, so until Prairie View A&M or AgriLife supplies measured
P and nitrate-N panels, public data adds provenance, spatial context, label
coverage, and crop presence more than it adds accuracy.

---

## Command reference

```bash
# Training
python train.py                      # 6000 synthetic samples only
python train.py --data-source real --out models/real_experiment.joblib
python train.py --data-source combined --out models/combined_experiment.joblib
python train.py --data-source combined --region fort_bend_only   # narrow comparison
python train.py --n-samples 8000     # more synthetic data
python train.py --seed 7             # a different synthetic draw

# Prediction
python predict.py --input <farm.csv>
python predict.py --input <farm.csv> --field-name "North 40" --outdir reports/

# Public-data ingestion (needs requirements-ingestion.txt)
python data/build_real_dataset.py             # fetch and standardize all five sources
python data/build_real_dataset.py --offline   # hash-verify and rebuild without network
```

Run the regression suite from the repository root:

```bash
python -m unittest discover -s backend/FarmersForwardModel/tests -v
```

---

## Notes & limitations

- Recommendations are **general agronomic guidance** based on Texas thresholds,
  not a substitute for a certified agronomist or a full lab panel.
- The cost/benefit section is **experimental** - order-of-magnitude ballparks
  using generic Texas input prices, explicitly **not financial advice**.
- Health labels use the rule oracle. Crop labels can add weighted positive-only CDL
  planting evidence; this does not establish crop yield success. Public-data
  experiments measure agreement with oracle/weak labels, not accuracy against
  independent field outcomes.
- Reported metrics use a random row split, not a geographic or per-pedon holdout,
  so they measure agreement with the oracle rather than field performance.
- Public data does not yet improve on synthetic-only training. See
  "What the public data can and cannot supply today" above for the P/K ceiling.

## About

FarmersFirst is a student-led nonprofit (Jordan High School, Fulshear, TX)
providing free, EC-based soil health assessments to small-scale farmers across
Fort Bend County and the Brazos River region.
