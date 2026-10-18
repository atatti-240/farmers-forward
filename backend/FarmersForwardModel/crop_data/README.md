# Farmers Forward — crop data

Crop-specific N/P/K, pH and EC thresholds for the assessment model.

| File | What it is |
|---|---|
| `crop_npk_tables.json` | All 24 Texas A&M fertilizer tables (8 categories × N/P/K), digitized. 340 crop/yield entries. Soil-test ppm → lbs to add, plus `sufficiency_ppm`, `units`, and A&M's application notes. |
| `crop_ph_ec_profiles.json` | 95 pH + salt-tolerance profiles, plus `crop_to_profile`, which maps all 340 A&M entries onto a profile. |
| `crop_ph_ec.csv` | The same profiles as a spreadsheet you can open and scan. |
| `crop_lookup.py` | Helper functions: `fert_rec()`, `ph_status()`, `salinity_status()`, and `assess()`. |

## Things to know before wiring this in

1. **The A&M tables are fertilizer *rate* tables, not "good ranges".** The "good" level is the ppm where the recommendation hits 0, stored as `sufficiency_ppm`. For corn, that's P ≈ 50 ppm and K ≈ 130 ppm. N almost never hits 0, because nitrate doesn't carry over, so A&M basically always recommends N.
2. **Lab methods.** The tables assume A&M lab numbers: P and K from **Mehlich III**, N from **KCl nitrate**. Our strip/AS7341 readings aren't the same extraction. Calibrate against a few samples sent to A&M before treating our ppm as theirs.
3. **EC must be ECe (saturated paste).** All published salt-tolerance data uses ECe. Convert first. **Our team measures 1:2 soil:water**, so `crop_lookup` uses ECe ≈ 3.0 × EC(1:2), an estimate for clay-heavy Gulf Coast soils. Replace it with our own factor once a few samples have lab saturated-paste results. Other methods: ECe ≈ 2.0 × EC(1:1) and ECe ≈ 6.4 × EC(1:5). These factors are rough and depend on texture.
4. **Data quality is tagged.** `ec_source`:
   - `MH` (63 profiles) = a published Maas–Hoffman threshold and slope.
   - `CLASS` (13) = only a published rating exists, so the threshold is the conservative low edge of that class.
   - `EST` (19) = estimated from a similar crop.

   `ph_confidence` flags the estimated pH ranges.
5. **Fixes to the source.** The A&M fiber-crop N PDF prints its row labels one row off. The parser corrects this, and the correction was verified against the continuation page. Rice has no N table; A&M points to the Texas Rice Production Guidelines.

```python
from crop_lookup import assess
assess("row", "CORN (150 BU/A)", ph=8.1, ec=1.0, ec_method="1:2", n_ppm=8, p_ppm=18, k_ppm=95)
```
