"""Regression checks for USDA units, geographic filtering, and missing data."""

import json
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from unittest import mock
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data import build_real_dataset as ingest
from pipeline import agronomy
from train import load_training_data
import train as training


class RealDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.survey = pd.DataFrame([{
            "mukey": "1", "cokey": "2", "chkey": "3", "hzname": "Ap",
            "ph": 6.5, "ec": 0.0, "om_pct": 2.0, "p_bray1": None, "cec": 25.0,
        }])

    def tearDown(self):
        self.temporary.cleanup()

    def lab_fixture(self):
        path = self.root / "fixture.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE lab_site (site_key INTEGER, latitude_std_decimal_degrees TEXT,
                    longitude_std_decimal_degrees TEXT, horizontal_datum_name TEXT);
                INSERT INTO lab_site VALUES (1, '29.5', '-95.8', 'NAD27');
                INSERT INTO lab_site VALUES (2, '29.5', '-90.0', 'NAD83');
                CREATE TABLE lab_pedon (pedon_key INTEGER, site_key INTEGER, user_pedon_id TEXT);
                INSERT INTO lab_pedon VALUES (1, 1, 'inside');
                INSERT INTO lab_pedon VALUES (2, 2, 'outside');
                CREATE TABLE lab_layer (layer_key INTEGER, pedon_key INTEGER, labsampnum TEXT,
                    hzn_desgn TEXT, hzn_top REAL, hzn_bot REAL);
                INSERT INTO lab_layer VALUES (1, 1, 'sample', 'Ap', 0, 10);
                INSERT INTO lab_layer VALUES (2, 2, 'outside', 'Ap', 0, 10);
                CREATE TABLE lab_column_descriptions (table_name TEXT, field_sequence INTEGER,
                    column_name TEXT, column_description TEXT);
            """)
            connection.execute("CREATE TABLE lab_chemical_properties_vw (layer_key INTEGER, %s)" %
                               ", ".join('"%s"' % name for name in ingest.KSSL_CHEM_COLUMNS))
            values = {"layer_key": 1, "objectid": 1, "prep_code": "S", "ph_h2o": 6.5,
                      "electrical_conductivity_satx": 0, "organic_carbon_walkley_black": 2,
                      "estimated_organic_carbon": 3, "phosphorus_bray1": 0,
                      "phosphorus_olsen": 10, "k_nh4_ph_7": 0.2}
            connection.execute("INSERT INTO lab_chemical_properties_vw (%s) VALUES (%s)" %
                               (", ".join(values), ", ".join("?" for _ in values)), list(values.values()))
        return ingest.extract_kssl(path)[0]

    def test_string_longitudes_are_filtered_numerically(self):
        lab = self.lab_fixture()
        self.assertEqual(len(lab), 1)
        self.assertEqual(lab.iloc[0].longitude, -95.8)

    def test_potassium_conversion_and_method_preference_preserve_zero(self):
        row = self.lab_fixture().iloc[0]
        self.assertAlmostEqual(row.k_extractable, 78.1966)
        self.assertEqual(row.k_method, "NH4OAc_pH7")
        self.assertEqual(row.p_extractable, 0)
        self.assertEqual(row.p_method, "Bray-1")
        self.assertEqual(row.ec, 0)
        self.assertEqual(row.organic_carbon_pct, 2)
        self.assertEqual(row.oc_method, "Walkley-Black")

    def test_disabled_derivation_and_no_potassium_from_cec(self):
        frame = ingest.standardize(self.survey, self.lab_fixture(), n_coefficient=None)
        self.assertTrue(frame.nitrogen.isna().all())
        self.assertFalse(frame.n_derived.any())
        self.assertTrue(pd.isna(frame.iloc[0].potassium))
        self.assertTrue(pd.isna(frame.iloc[0].phosphorus))
        self.assertTrue(pd.isna(frame.iloc[0].latitude))

    def test_user_formula_is_default_for_om_and_converted_oc(self):
        self.assertEqual(agronomy.nitrogen_credit_from_organic_matter(2), 30)
        self.assertEqual(agronomy.nitrogen_credit_from_organic_matter(0), 0)
        frame = ingest.standardize(self.survey, self.lab_fixture())
        self.assertEqual(frame.iloc[0].nitrogen, 30)
        self.assertAlmostEqual(frame.iloc[1].nitrogen, 2 * 1.724 * 15)
        self.assertTrue(frame.n_derived.all())
        self.assertFalse(frame.iloc[0].om_derived)
        self.assertTrue(frame.iloc[1].om_derived)

    def test_explicit_derivation_flags_and_measured_nitrogen_precedence(self):
        lab = self.lab_fixture()
        frame = ingest.standardize(self.survey, lab, n_coefficient=10, oc_to_om=2)
        self.assertEqual(frame.nitrogen.tolist(), [20, 40])
        self.assertTrue(frame.n_derived.all())
        self.assertTrue(frame.iloc[1].om_derived)
        lab["nitrate_n"] = 17
        measured = ingest.standardize(self.survey, lab, n_coefficient=10, oc_to_om=2).iloc[1]
        self.assertEqual(measured.nitrogen, 17)
        self.assertFalse(measured.n_derived)

    def test_invalid_mineralization_parameters(self):
        for om, coefficient in [(float("nan"), 10), (-1, 10), (101, 10), (1, 0), (1, float("inf"))]:
            with self.assertRaises(ValueError):
                agronomy.nitrogen_credit_from_organic_matter(om, n_ppm_per_om_pct=coefficient)

    def test_missing_carbon_stays_missing_and_total_n_is_not_used(self):
        lab = self.lab_fixture()
        lab["organic_carbon_pct"] = None
        lab["total_nitrogen_ncs"] = 1.5
        result = ingest.standardize(self.survey, lab, n_coefficient=10, oc_to_om=2).iloc[1]
        self.assertTrue(pd.isna(result.nitrogen))
        self.assertFalse(result.n_derived)

    def test_synthetic_does_not_read_real_files(self):
        frame, counts = load_training_data("synthetic", 10, 42, self.root / "absent.csv", self.root)
        self.assertEqual(len(frame), 10)
        self.assertEqual(set(counts), {"synthetic"})

    def test_partial_and_invalid_real_rows_excluded_before_imputation(self):
        values = {"ph": 6.5, "ec": 1, "nitrogen": 20, "phosphorus": 20,
                  "potassium": 150, "source": "kssl"}
        rows = [values.copy() for _ in range(6)]
        rows.append({**values, "potassium": None})
        rows.append({**values, "ph": 99})
        path = self.root / "training.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        real, counts = load_training_data("real", 10, 42, path, self.root / "farm")
        self.assertEqual(len(real), 6)
        self.assertEqual(counts["kssl"], {"available": 8, "used": 6, "excluded": 2})
        combined, counts = load_training_data("combined", 10, 42, path, self.root / "farm")
        self.assertEqual(len(combined), 16)
        self.assertEqual(counts["synthetic"]["used"], 10)

    def test_real_fails_clearly_and_combined_reports_synthetic_only(self):
        frame = ingest.standardize(self.survey, self.lab_fixture(), n_coefficient=None)
        path = self.root / "training.csv"
        frame.to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, "No complete"):
            load_training_data("real", 10, 42, path, self.root / "farm")
        with self.assertWarnsRegex(UserWarning, "synthetic rows only"):
            combined, counts = load_training_data("combined", 10, 42, path, self.root / "farm")
        self.assertEqual(len(combined), 10)
        self.assertEqual(counts["kssl"]["used"], 0)

    def test_training_preserves_canonical_nitrogen_with_raw_nitrate_provenance(self):
        frame = ingest.standardize(self.survey, self.lab_fixture())
        path = self.root / "training.csv"
        frame.to_csv(path, index=False)
        def reached_models(labeled, **kwargs):
            self.assertTrue(labeled.columns.is_unique)
            row = labeled.loc[labeled.source.eq("kssl")].iloc[0]
            self.assertAlmostEqual(row.nitrogen, 2 * 1.724 * 15)
            self.assertTrue(pd.isna(row.nitrate_n))
            self.assertTrue(row.n_derived)
            raise RuntimeError("validated real rows reached models")
        argv = ["train.py", "--data-source", "combined", "--n-samples", "5000",
                "--real-data", str(path), "--farm-data", str(self.root / "farm")]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), \
                mock.patch.object(training.M, "train_models", side_effect=reached_models):
            with self.assertRaisesRegex(RuntimeError, "validated real rows reached models"):
                training.main()

    def test_empty_sda_payload_is_retried_and_never_cached(self):
        """SDA answers {} under load. That is transient, not a verdict of no data."""
        cache = self.root / "sda.json"
        attempts = []

        def respond(payloads):
            def opener(request, timeout=None):
                payload = payloads[min(len(attempts), len(payloads) - 1)]
                attempts.append(payload)
                return io.BytesIO(json.dumps(payload).encode())
            return opener

        table = {"Table": [["mukey"], ["1"]]}
        with mock.patch("time.sleep"), \
                mock.patch("urllib.request.urlopen", respond([{}, {}, table])):
            frame = ingest.sda_query("SELECT 1", cache)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(frame.mukey.tolist(), ["1"])
        self.assertEqual(json.loads(cache.read_text()), table)

        attempts.clear()
        cache.unlink()
        with mock.patch("time.sleep"), mock.patch("urllib.request.urlopen", respond([{}])):
            with self.assertRaisesRegex(ValueError, "no Table"):
                ingest.sda_query("SELECT 1", cache)
        self.assertEqual(len(attempts), 3)
        # A persistent failure must not poison the cache with an empty answer.
        self.assertFalse(cache.exists())

    def test_incomplete_rows_never_reach_default_imputation(self):
        """DEFAULTS would silently replace a missing lab value with 20/20/150."""
        path = self.root / "real.csv"
        pd.DataFrame([
            {"sample_id": "gap", "source": "kssl", "ph": 6.5, "ec": 1.0,
             "nitrogen": 30.0, "phosphorus": None, "potassium": 150.0},
        ]).to_csv(path, index=False)
        argv = ["train.py", "--data-source", "combined", "--n-samples", "5000",
                "--real-data", str(path), "--farm-data", str(self.root / "absent")]
        seen = {}

        def capture(df, seed=None, verbose=True):
            seen["rows"] = len(df)
            raise RuntimeError("stop after cleaning")

        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), \
                mock.patch.object(training.M, "attach_labels", side_effect=capture):
            with self.assertRaisesRegex(RuntimeError, "stop after cleaning"):
                training.main()
        # The partial row was dropped, so cleaning saw synthetic rows only and
        # no phosphorus default was invented for it.
        self.assertEqual(seen["rows"], 5000)

        # If the complete-case filter ever regresses, cleaning must fail loudly
        # rather than impute the gap.
        frame = pd.DataFrame({"ph": [6.5], "ec": [1.0], "nitrogen": [30.0],
                              "phosphorus": [None], "potassium": [150.0],
                              "source": ["kssl"]})
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), \
                mock.patch.object(training, "load_training_data",
                                  return_value=(frame, {})):
            with self.assertRaisesRegex(ValueError, "phosphorus"):
                training.main()

    def test_region_tier_precedence_and_final_weight_math(self):
        """Fort Bend outranks its MLRA peers; weights multiply, never replace."""
        self.assertEqual(ingest.tier_from_areas(
            ["TX157", "TX201", None, "LA001"]).tolist(),
            ["fort_bend", "same_mlra", "gulf_coast_regional", "same_mlra"])
        frame = pd.DataFrame({
            "region_tier": ["fort_bend", "fort_bend", "same_mlra", "gulf_coast_regional"],
            "evidence_weight": [1.0, 0.5, 1.0, 0.5],
            "n_derived": [False, True, True, True],
            "signal_strength": [None, "presence_only", None, "presence_only"],
        })
        weighted = ingest.apply_region_weights(frame)
        self.assertEqual(weighted.region_weight.tolist(), [1.0, 1.0, 0.7, 0.4])
        self.assertEqual(weighted.final_weight.tolist(), [1.0, 0.5, 0.7, 0.2])
        # The new column sits beside the provenance flags, it does not replace them.
        self.assertEqual(weighted.n_derived.tolist(), frame.n_derived.tolist())
        self.assertEqual(weighted.signal_strength.tolist(), frame.signal_strength.tolist())
        self.assertEqual(weighted.evidence_weight.tolist(), frame.evidence_weight.tolist())
        with self.assertRaisesRegex(ValueError, "No region weight defined"):
            ingest.apply_region_weights(pd.DataFrame(
                {"region_tier": ["west_texas"], "evidence_weight": [1.0]}))

    def test_saved_region_tiers_match_their_sources(self):
        """Each tier holds only the sources that can legitimately supply it."""
        directory = ROOT / "data" / "real"
        narrow = pd.read_csv(directory / "fort_bend_real_training.csv", low_memory=False)
        self.assertEqual(set(narrow.region_tier), {"fort_bend"})
        self.assertTrue(narrow.region_weight.eq(1.0).all())
        # The narrow dataset must keep training exactly as it did before tiering.
        self.assertTrue(narrow.final_weight.equals(narrow.sample_weight))

        expanded = pd.read_csv(directory / "expanded_real_training.csv", low_memory=False)
        self.assertEqual(set(expanded.region_tier), set(ingest.REGION_WEIGHTS))
        for tier, weight in ingest.REGION_WEIGHTS.items():
            rows = expanded.loc[expanded.region_tier.eq(tier)]
            self.assertTrue(rows.region_weight.eq(weight).all(), tier)
            self.assertTrue(
                rows.final_weight.round(6).eq((rows.region_weight * rows.evidence_weight).round(6)).all(), tier)
        self.assertTrue(expanded.sample_id.is_unique)
        # Widening the region is only worth it if it produces complete rows.
        self.assertGreater(int(expanded[ingest.preprocess.FEATURE_COLUMNS].notna().all(axis=1).sum()),
                           int(narrow[ingest.preprocess.FEATURE_COLUMNS].notna().all(axis=1).sum()))

    def test_fort_bend_only_region_selects_the_narrow_dataset(self):
        """The comparison path still reads the original file and its weights."""
        self.assertEqual(training.REGION_DATASETS["fort_bend_only"].name,
                         "fort_bend_real_training.csv")
        self.assertEqual(training.REGION_DATASETS["expanded"].name,
                         "expanded_real_training.csv")
        seen = {}

        def capture(df, seed=None, verbose=True):
            seen["rows"] = len(df)
            seen["weights"] = sorted(set(df["final_weight"].round(4)))
            raise RuntimeError("stop at training")

        for region, expected_real in (("fort_bend_only", 2), ("expanded", 307)):
            argv = ["train.py", "--data-source", "combined", "--n-samples", "5000",
                    "--region", region, "--farm-data", str(self.root / "absent")]
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), \
                    mock.patch.object(training.M, "train_models", side_effect=capture):
                with self.assertRaisesRegex(RuntimeError, "stop at training"):
                    training.main()
            self.assertEqual(seen["rows"], 5000 + expected_real, region)
        # Only the expanded run carries the reduced regional weights. 0.2
        # (0.4 * 0.5) is absent because CDL context covers Fort Bend map units
        # only, so no regional row is presence_only.
        self.assertEqual(seen["weights"], [0.4, 0.5, 0.7, 1.0])

    def test_snapshot_counts_and_no_em_dashes(self):
        directory = ROOT / "data" / "real"
        frame = pd.read_csv(directory / "fort_bend_real_training.csv", low_memory=False)
        saved = json.loads((directory / "summary.json").read_text())
        self.assertEqual(ingest.summarize(frame), saved["total"])
        self.assertTrue(frame.sample_id.is_unique)
        self.assertTrue(frame.loc[frame.source.eq("ssurgo"), "potassium"].isna().all())
        lab = frame.loc[frame.source.eq("kssl")]
        self.assertTrue(lab.latitude.between(29.3, 29.9).all())
        self.assertTrue(lab.longitude.between(-96.3, -95.4).all())
        for path in directory.glob("*.csv"):
            self.assertNotIn(chr(0x2014), path.read_text())


if __name__ == "__main__":
    unittest.main()
