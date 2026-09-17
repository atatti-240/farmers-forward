# Fort Bend public soil data

These files contain USDA survey/lab records, ISRIC model predictions, and spatial
context, including their missing measurements. They are not a complete replacement for the synthetic
training baseline. No API key is needed.

## Reproduce the export

Install the optional spatial-ingestion requirements, then run from the model directory:

```bash
python -m pip install -r requirements-ingestion.txt
python data/build_real_dataset.py
python data/build_real_dataset.py --offline
```

The first command downloads the national KSSL SQLite ZIP (about 1.96 GB compressed,
5.41 GB extracted in the initial download) into `~/.cache/farmers-forward-usda/`.
It also caches SDA responses, SoilGrids subsets, and the county CDL raster.
Existing regional exports are hash-verified and reused. `--refresh` refetches all sources. `--kssl-db PATH`
uses an existing KSSL SQLite database. `--cache-dir PATH` changes the cache location.
The cache is outside the repository; only regional CSVs and provenance are tracked.
`--offline` verifies raw-file hashes and rebuilds the standardized file without
network access. All paths default relative to the script, independent of cwd.

Outputs:

- `ssurgo_fort_bend.csv`: requested SSURGO fields, one record per horizon or
  component lacking horizon data. The left joins preserve every map unit.
- `kssl_fort_bend.csv`: regional laboratory layers, original measurement fields,
  method codes, preparation codes, IDs, depth, coordinates, and unit-normalized aliases.
- `soilgrids_fort_bend.csv`: 250 m cell centers, both requested depth intervals,
  converted properties, predicted total N, and containing map unit where available.
- `cdl_crop_by_mukey.csv`: 2025 dominant planted crop, percentage, and
  `signal_strength=presence_only` on every row, including units without crop pixels.
- `wss_interpretive_fort_bend.csv`: dominant-component drainage/flood attributes,
  surface-horizon erosion K, T factor, and nullable soil-health rating.
- `fort_bend_real_training.csv`: soil observations/predictions plus spatial context,
  with nulls, N provenance, `signal_strength`, and `sample_weight` retained.
  Every row is `region_tier=fort_bend`, so `final_weight` equals `sample_weight`.
- `ssurgo_mlra_region.csv`: SSURGO horizons for all 40 survey areas in MLRA 150A,
  tiered per row so TX157 keeps `fort_bend`.
- `kssl_gulf_region.csv`: KSSL lab layers across the wider Gulf box, tiered by a
  survey-area outline lookup.
- `soilgrids_gulf_region.csv`: SoilGrids predictions at the native cells holding a
  Gulf-region KSSL pedon, outside the Fort Bend window.
- `expanded_real_training.csv`: all three tiers merged, carrying `region_tier`,
  `region_weight`, `evidence_weight`, and `final_weight`.
- `summary.json`: source counts, missingness, completeness, and observed coverage.
- `provenance.json`: source URLs, exact queries, retrieval/build times, file hashes,
  coordinate definitions, and selected USDA field definitions.

## Sources and units

SSURGO uses the [USDA SDA REST/POST service](https://sdmdataaccess.sc.egov.usda.gov/WebServiceHelp.aspx),
joining `legend -> mapunit -> component -> chorizon` for `areasymbol = 'TX157'`.
The physical representative-value columns are `ph1to1h2o_r`, `ec_r`, `om_r`,
`pbray1_r`, and `cec7_r`. They become `ph`, `ec`, `om_pct`, `p_bray1`, and `cec`.
SSURGO representative values describe survey components, not individual lab samples.
CEC is retained as auxiliary information and never becomes potassium or a model feature.
There are no point coordinates in this horizon query. Its map-unit polygon coverage
is reported separately instead of inventing sample coordinates.

KSSL comes from the [NCSS Lab Data Mart bulk SQLite download](https://ncsslabdatamart.sc.egov.usda.gov/database_download.aspx).
The geographic filter is latitude 29.3 to 29.9, longitude -96.3 to -95.4. SQLite
stores the standardized coordinate fields as text, so the SQL explicitly casts
them to numbers. The standardized coordinates are WGS84; the original datum is
retained separately. The box includes adjacent counties, not just Fort Bend.

Mappings follow the downloaded `lab_column_descriptions` and `lab_analyte` metadata:

| Input | Mapping and units |
| --- | --- |
| pH | `ph_h2o`: 1:1 soil-water pH. No substitution of KCl or CaCl2 pH. |
| EC | `electrical_conductivity_satx`: saturation-extract mmhos/cm, numerically equal to dS/m. No substitution of predicted 1:2 EC. |
| Organic carbon | Prefer Walkley-Black; otherwise retain USDA's `estimated_organic_carbon`, explicitly identified by `oc_method`. Both are percent by mass. Total carbon is not substituted. |
| Phosphorus | Prefer Bray-1, then Mehlich-3, then Olsen, in mg/kg (ppm). The selected extraction method and original values remain available. Oxalate P and total P are not plant-available P substitutes. Only 2 of 311 layers carry any extractable P, both Olsen. |
| Potassium | NH4OAc pH 7 extractable K in meq/100 g multiplied by 390.983 to obtain mg/kg. Fall back only to measured Mehlich-3 K (already mg/kg). |
| Nitrogen | Measured `nitrate_1m_kcl` is NO3-N in mg/kg. Total nitrogen and nitrate in solution units are not substituted. No matching measured nitrate-N values were present in this snapshot. |

The ingestion stacks observations. It does not join unrelated SSURGO horizons and
KSSL pedons together to manufacture complete five-input rows. Multiple horizons
from the same pedon are related observations, not independent farms. Different
P extraction methods are retained for experimental comparisons; their agronomic
calibration is not identical. The current pipeline does not calibrate between methods.

## Nitrogen credit

The shared `pipeline/agronomy.py` helper defaults to the user-specified formula:

```text
Available N (lb/acre/year) = OM% * 20,000 * 0.05 * 0.03 = OM% * 30
Available N credit (ppm) = OM% * 30 / 2 = OM% * 15
```

OM is entered as percentage points: 2% organic matter gives a 30 ppm N credit.
This approximates 3% annual mineralization and uses 2 lb/acre per ppm. These are
modeling assumptions for the annual credit proxy, not a depth-specific laboratory
nitrate measurement for each horizon.

For KSSL, organic carbon is converted to estimated organic matter using
`OM% = OC% * 1.724`, following the [USDA NRCS National Soil Survey Handbook,
Part 618](https://directives.nrcs.usda.gov/sites/default/files2/1719846855/Part%20618%20Subpart%20A%20%E2%80%93%20General%20Information.pdf).
The source OC value and method are retained; converted OM has `om_derived = True`.
The coefficients live in `pipeline/thresholds.py`. Ingestion flags
`--n-credit-ppm-per-om-pct VALUE` and `--oc-to-om-factor VALUE` allow explicit
experimental overrides; their defaults are 15 and 1.724, respectively.

Derived rows have `n_derived = True` and `n_method = OM_mineralization_credit_proxy`.
Measured nitrate-N takes precedence and keeps `n_derived = False`. Missing or
invalid OM stays missing; absent OC does not become zero. A mineralized N credit
is a proxy, not a measured nitrate concentration. Parameters are saved in both
the data and provenance.

## SoilGrids, CDL, and WSS spatial joins

[ISRIC SoilGrids v2](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html)
is a prediction product, not measured lab data. One REST request verifies native
units; ten [WCS subsets](https://docs.isric.org/globaldata/soilgrids/wcs.html) retrieve
five properties at 0-5 and 5-15 cm without flooding the rate-limited point API.
Cells use the native interrupted Goode homolosine grid at exactly 250 m, with
centers restricted to the requested WGS84 box. `source_depth_cm` is `0-5` or `5-15`.
The REST point result was cross-checked against its WCS cell for all ten values.
Background cells are retained with nulls, including 39,548 depth records without pH.

| Property | Native integer conversion | Stored field |
| --- | --- | --- |
| phh2o | divide by 10 | ph |
| soc | dg/kg divided by 100 to percent | organic_carbon_pct |
| nitrogen | cg/kg multiplied by 10 to mg/kg | soilgrids_total_nitrogen_ppm |
| cec | mmol(c)/kg divided by 10 to cmol(c)/kg | cec |
| bdod | cg/cm3 divided by 100 to g/cm3 | bulk_density_g_cm3 |

**Total N is chemically different from available nitrate-N.** The requested
SoilGrids-first measured-N priority cannot be applied literally: SoilGrids supplies
predicted total N, not measured nitrate. Its converted total N remains auxiliary;
KSSL measured nitrate takes precedence in the model input, followed by the
explicitly flagged OM-derived credit when measured available N is absent.
SoilGrids has no EC, extractable P, or extractable K layers. Those remain null.
KSSL extractable K remains authoritative on lab rows; CEC never supplies K.
The OM fallback on a SoilGrids row is derived from predicted OC and is identified
by `record_kind=model_prediction` and `om_method`, never as a lab measurement.

SDA map-unit polygons assign KSSL points and SoilGrids cell centers to `mukey`.
Outside-county points remain unmatched. Drainage and CDL join many-to-one on
mukey; no laboratory measurement is broadcast to every grid cell in a map unit.
For KSSL horizons fully within 0-15 cm, auxiliary SoilGrids properties are sampled
from the same native cell and weighted by depth overlap. Deeper layers are not
extrapolated. The contributing grid sample IDs and join method remain in the CSV.
Survey horizons retain no artificial coordinates. Context joins do not multiply
sample identities, and a shared map unit does not imply identical soil chemistry.

[USDA CroplandCROS](https://croplandcros.scinet.usda.gov/) supplies the latest
catalog year, 2025 in this run. The legacy CropScape service failed certificate
validation; the current official USDA ImageServer worked without an API key.
The county raster is exported at 10 m in equal-area EPSG:5070 with nearest-neighbor
resampling and clipped to the Census Fort Bend boundary (GEOID 48157). This is a
service-resampled grid, not a claim of native-pixel alignment. The cached clipped
GeoTIFF is outside Git; its location and source catalog item are in provenance.

Dominant crop is the most common crop class across all polygons for a mukey.
Water/developed/forest/pasture/fallow classes are excluded as candidate crops.
`crop_pixel_pct = 100 * dominant_crop_pixels / all_valid_map_unit_pixels`;
the denominator includes valid noncrop land. Pixel-center inclusion is used;
ties select the lowest crop code. Units with no crop pixels retain a null crop.

Every CDL export row and every row joined to it carries
`signal_strength=presence_only`. Planting is not confirmed yield success.
Rows whose only crop evidence is CDL have `sample_weight=0.5`; rows without CDL
crop dependency use 1.0. All three estimators receive these explicit weights.
For model-supported crop names, CDL adds only the planted crop's weak positive
target. It supplies no negative targets for unplanted crops; other targets retain
oracle labels. Unmapped crop names remain context and do not create new targets.
Real Prairie View/AgriLife trials must replace matched CDL labels entirely,
not create duplicate training rows. Explicit `crop_signal_source=yield|trial`
plus binary `observed_crop_<CropName>` outcomes override weak labels at weight 1.0.
Missing observed outcomes retain identified oracle labels, not invented failures.

WSS drainage/flood/erosion attributes succeeded using the underlying SSURGO data
via SDA, a scriptable alternative to interactive AOI export. This is the same
survey source, not a fifth independent measurement collection. The component
with greatest reported map-unit percentage is selected; erosion K comes from
its shallowest horizon, and flood frequency is its highest monthly category.
Flood category `None` means no flooding and is preserved as text, distinct from null.
K is an erosion factor here, not nutrient potassium. Soil-health rating is null:
these tables provide no generic rating, and the county cointerp query for rule
names containing `health` returned no records. NCCPI is not substituted.

If scripted WSS access fails, ingestion writes a header-only interpretive CSV,
records `status=skipped` and the reason, and continues. Manual fallback:

1. Open [Web Soil Survey](https://websoilsurvey.nrcs.usda.gov/app/), choose Texas and
   Fort Bend County, and set the county/survey area as the AOI.
2. In Soil Data Explorer, select Soil Properties and Qualities and export drainage
   class, erosion K/T, and flooding-frequency ratings, retaining map-unit IDs.
3. Use Download Soils Data for the AOI, or Soil Survey Area download for TX157,
   to obtain the spatial/tabular survey package. Keep the package date and methods.
4. Normalize the exported ratings to the documented interpretive CSV schema and
   ingest with provenance. Do not invent a soil-health rating if none is offered.

## Download coverage

| Source | Rows | Complete five-input rows | Partial soil rows |
| --- | ---: | ---: | ---: |
| SSURGO | 333 | 0 | 333 |
| KSSL | 311 | 2 | 309 |
| SoilGrids | 186,062 | 0 | 186,062 |
| CDL map-unit context | 51 | n/a | n/a |
| WSS map-unit context | 51 | n/a | n/a |
| Final soil dataset | 186,706 | 2 | 186,704 |

CDL and WSS are attached context, not 102 extra soil measurements. SSURGO has
51 map units, 216 horizons, and 117 component records without horizons. KSSL has
41 pedons and 311 layers. Nitrogen inputs: 0 measured, 147,025 OM-derived,
39,681 null. Derived counts are 211 SSURGO, 300 KSSL, and 146,514 SoilGrids.
73,215 final rows carry CDL provenance; 73,030 have a non-null dominant crop and
weight 0.5. The other 113,676 rows have weight 1.0. Both complete KSSL records
carry Corn presence evidence with weight 0.5.

- Actual grid/sample centers: latitude **29.30192167 to 29.89930134**, longitude
  **-96.29999724 to -95.40000196**.
- KSSL points: latitude **29.349697 to 29.892584**, longitude
  **-96.294777 to -95.445389**.
- SSURGO polygon extent: latitude **29.262431885 to 29.788439395**, longitude
  **-96.081778150 to -95.426523402**.

These are actual exported extents. The requested box also covers adjacent counties.
See `summary.json` for missing-field counts and source status, and `provenance.json`
for exact queries, raster parameters, unit metadata, and regional CSV hashes.

## Why only two rows carry all five inputs

This is a property of the public sources, not a join defect. Per-input coverage
across the 186,706 final rows:

| Input | Rows with a value | Supplying sources |
| --- | ---: | --- |
| pH | 147,029 | SoilGrids 146,514, SSURGO 211, KSSL 304 |
| EC | 355 | SSURGO 211, KSSL 144. SoilGrids has no EC layer |
| Nitrogen | 147,025 | all OM-derived: SoilGrids 146,514, KSSL 300, SSURGO 211 |
| Phosphorus | 2 | KSSL Olsen only |
| Potassium | 304 | KSSL NH4OAc pH 7 only |

This is what motivated the regional expansion below, which raises the count from
2 to 307. Within Fort Bend itself the ceiling is unchanged.

Phosphorus is the binding constraint. SSURGO `pbray1_r` is null for all 333
TX157 horizon records, SoilGrids publishes no extractable-P layer, and the KSSL
Fort Bend extract holds 2 Olsen-P values across 41 pedons and 311 layers. The
two complete rows are consecutive layers of one pedon, so they are not two
independent fields. No substitution closes this gap: total P, oxalate P, and
CEC are different quantities, and copying a lab P value across a map unit would
fabricate measurements. Measured P panels from Prairie View A&M or AgriLife are
the only thing that raises this ceiling.

Potassium coverage (304 rows) is adequate; nitrogen is fully derived rather than
measured. `summary.json` records this directly:

- `phosphorus_potassium_coverage`: non-null P and K counts per source.
- `nitrogen_measured_coverage`: the measured-nitrate count (0), the layers
  queried (311), the retained SoilGrids total-N count and median, the reason
  total N is excluded, and derived counts per source.

## Regional expansion: MLRA 150A and the wider Gulf tier

Fort Bend County is not large enough to supply a training set on its own, so the
ingestion widens the region in two tiers of decreasing confidence. Neither tier
was picked by hand.

**Tier 2, same MLRA.** A Major Land Resource Area groups land by shared soil,
climate, and water resources, which is a defensible similarity claim where a list
of neighbouring states is not. The MLRA is derived from SDA rather than assumed:

```sql
SELECT lo.areasymbol, lo.areaname FROM legend l
JOIN laoverlap lo ON lo.lkey = l.lkey
WHERE l.areasymbol = 'TX157' AND lo.areatypename = 'MLRA'
```

TX157 returns exactly one row: **MLRA 150A, Gulf Coast Prairies**. Querying back
the other way gives **40 soil survey areas** that share it, spanning the Texas
Gulf Coast from Nueces to Jefferson County and thirteen Louisiana parishes. The
`US` legend is excluded: it is the STATSGO2 national legend (areatypename
`Country`, 9,562 generalized map units), not a soil survey area, and it overlaps
every MLRA in the country.

**Tier 3, Gulf Coast regional.** A wider box, 28 to 33 N and 98 to 82 W, covering
the Gulf Coast clay belt into Louisiana, Mississippi, Alabama, and the Florida
panhandle. This is a broader net with lower confidence of true similarity. It is
kept because it carries most of the measured phosphorus, and it is weighted down
rather than treated as equivalent evidence. Trimming the northern edge to 31 N
would drop 164 of the 307 complete rows, so the full box earns its place.

Tier is decided by where a row actually is, not by which file it arrived in.
Both regional pulls are supersets of the Fort Bend queries, so a KSSL pedon
inside the Fort Bend bounding box but in Harris or Waller County is tiered
`same_mlra`, not `fort_bend`. Precedence is Fort Bend, then MLRA peer, then the
wider box.

| region_tier | Rows | Complete five-input rows | region_weight | Sources |
| --- | ---: | ---: | ---: | --- |
| `fort_bend` | 186,577 | 2 | 1.0 | SoilGrids 186,062, SSURGO 333, KSSL 182 |
| `same_mlra` | 18,529 | 114 | 0.7 | SSURGO 14,808, KSSL 3,093, SoilGrids 628 |
| `gulf_coast_regional` | 19,208 | 191 | 0.4 | KSSL 15,084, SoilGrids 4,124 |
| **Total** | **224,314** | **307** | | |

`same_mlra` rows are the higher-confidence regional match: same MLRA, same soil
and climate grouping as Fort Bend. `gulf_coast_regional` rows are a broader,
lower-confidence supplement that adds volume and most of the measured P. The
weights encode exactly that difference and are the three constants
`REGION_WEIGHT_FORT_BEND`, `REGION_WEIGHT_SAME_MLRA`, and
`REGION_WEIGHT_REGIONAL` at the top of `build_real_dataset.py`.

Phosphorus, still the binding constraint, goes from 2 rows to 1,506: 409 in the
MLRA tier and 1,095 in the wider box. Potassium goes from 182 to 15,636. Measured
nitrate-N remains 0 across all 18,359 Gulf-region KSSL layers, so the Fort Bend
finding was not a local quirk: KSSL simply does not run KCl nitrate on these
archived pedons.

### Weighting

`region_weight` is an additional axis, not a replacement. `n_derived`,
`n_source`, `signal_strength`, and the CDL `sample_weight` keep their existing
meanings, and the models fit on the product:

```text
final_weight = region_weight * evidence_weight
```

`evidence_weight` is 1.0 normally and 0.5 for a row whose only crop evidence is
CDL planting presence. So a Fort Bend row with real evidence counts 1.0, an MLRA
row counts 0.7, a wider-box row counts 0.4, and a Fort Bend CDL-presence row
counts 0.5. The combination 0.2 does not occur in this snapshot because CDL and
WSS context is joined by Fort Bend map unit, so no regional row is
presence-only. Regional rows therefore carry null crop and interpretive columns.

### SoilGrids in the regional tier

The regional SoilGrids export samples only the native 250 m cells that contain a
KSSL pedon, 2,376 cells, rather than exporting the full box. A standalone grid
cell can never complete a five-input row, because SoilGrids publishes no EC,
extractable P, or extractable K layer; its only path into the model is the
depth-overlap join that attaches predicted pH, OC, and CEC to a measured KSSL
horizon. Exporting the whole box at native resolution would have produced
28,210,936 rows and roughly 3.3 GB for no additional trainable row. Cells inside
the Fort Bend window are skipped because that window already has full-resolution
coverage. The `soilgrids_gulf:` sample-id prefix keeps these cells distinct from
the Fort Bend grid, whose row and column indices are relative to a different
window origin.

## Transient SDA failures

The SDA endpoint answers a well-formed request with an empty `{}` object when it
is under load. Ingestion treats that as transient and retries it with the same
exponential backoff as a dropped connection, up to three attempts, and never
caches an empty answer. A payload that stays empty across all three raises
`SDA returned no Table`. For the SSURGO and polygon queries that aborts the run,
because the run cannot proceed without them. For the WSS interpretive query it is
caught, `status=skipped` and the reason are written to `provenance.json` and
`summary.json`, a header-only CSV is produced, and the build continues. The
failure is always recorded; it is never silently swallowed.

## Training comparisons

```bash
python train.py --data-source synthetic --out models/synthetic_experiment.joblib
python train.py --data-source real --out models/real_experiment.joblib
python train.py --data-source combined --out models/combined_experiment.joblib

# Narrow vs expanded region, for a direct A/B comparison
python train.py --data-source combined --region fort_bend_only --out models/narrow.joblib
python train.py --data-source combined --region expanded --out models/expanded.joblib
```

`--region expanded` is the default and reads `expanded_real_training.csv`.
`--region fort_bend_only` reads the original `fort_bend_real_training.csv` and
is kept unchanged so the two can be compared. An explicit `--real-data PATH`
overrides both.

Synthetic remains the default and never reads the real dataset. Real and combined
modes also accept extra CSVs under `data/farm_data/`. Real rows must have all five
inputs, finite and within the model's existing bounds, before they reach the
oracle. They are never filled with default nutrients or clipped to appear valid.
Available, used, and excluded counts are printed and saved in model metadata.

For this snapshot, `real` exits because only two complete records qualify, below
the five-row minimum, and does not write a model. `combined` includes those two
KSSL records with the synthetic samples. Both records are from the same pedon.
Sparse phosphorus remains the main constraint; more complete local panels are
needed for a meaningful A/B comparison.

Health labels still come from the deterministic agronomy rules. Crop targets can
add the explicitly weighted positive-only CDL evidence described above. None of
these public sources adds observed yield or soil-health outcome labels. Existing
metrics use a random row split, not an independent geographic or pedon holdout.
They measure agreement with oracle/weak labels and cannot establish field performance.

Run regression checks from the repository root:

```bash
python -m pip install -r backend/FarmersForwardModel/requirements-ingestion.txt
python -m unittest discover -s backend/FarmersForwardModel/tests -v
```

`tests/test_spatial_sources.py` imports `data/spatial_sources.py`, which needs
`rasterio`, `shapely`, and `pyproj`. Without the ingestion requirements that
module fails to import and the test run reports one error while the other tests
still pass.
