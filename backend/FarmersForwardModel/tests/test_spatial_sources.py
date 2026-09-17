"""Protect units, spatial joins, positive-only crop evidence, and fit weights."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data import spatial_sources as spatial
from pipeline import evidence, model, thresholds as T
from train import synthesize_samples


class SpatialSourceTests(unittest.TestCase):
    def test_soilgrids_unit_conversions_and_changed_unit_rejection(self):
        units = {"phh2o": {"mapped_units": "pH*10", "d_factor": 10},
                 "soc": {"mapped_units": "dg/kg", "d_factor": 10},
                 "nitrogen": {"mapped_units": "cg/kg", "d_factor": 100},
                 "cec": {"mapped_units": "mmol(c)/kg", "d_factor": 10},
                 "bdod": {"mapped_units": "cg/cm³", "d_factor": 100}}
        native = {"phh2o": np.array([65., np.nan]), "soc": np.array([200., np.nan]),
                  "nitrogen": np.array([150., np.nan]), "cec": np.array([250., np.nan]),
                  "bdod": np.array([130., np.nan])}
        converted = spatial.convert_soilgrids(native, units)
        self.assertEqual(converted["ph"][0], 6.5)
        self.assertEqual(converted["organic_carbon_pct"][0], 2)
        self.assertEqual(converted["soilgrids_total_nitrogen_ppm"][0], 1500)
        self.assertEqual(converted["cec"][0], 25)
        self.assertEqual(converted["bulk_density_g_cm3"][0], 1.3)
        self.assertTrue(all(np.isnan(v[1]) for v in converted.values()))
        self.assertNotIn("nitrogen", converted)
        units["nitrogen"]["d_factor"] = 10
        with self.assertRaisesRegex(ValueError, "units changed"):
            spatial.convert_soilgrids(native, units)

    def test_location_join_does_not_assign_outside_points(self):
        polygons = spatial.SoilPolygons(pd.DataFrame({"mukey": ["1", "2"], "polygon_wkt": [
            "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))",
            "POLYGON ((1 0, 2 0, 2 1, 1 1, 1 0))"]}))
        result = polygons.locate([.5, 1.5, 3, 1], [.5, .5, .5, .5])
        self.assertEqual(result[0], "1")
        self.assertEqual(result[1], "2")
        self.assertTrue(pd.isna(result[2]))
        self.assertEqual(result[3], "1")

    def test_dominant_crop_excludes_non_crop_and_flags_every_row(self):
        frame = spatial.crop_summary({"1": {1: 20, 2: 10, 111: 70}, "2": {111: 30}, "3": {}},
                                     {1: "Corn", 2: "Cotton", 111: "Open Water"}, 2025)
        self.assertEqual(frame.iloc[0].dominant_crop, "Corn")
        self.assertEqual(frame.iloc[0].crop_pixel_pct, 20)
        self.assertEqual(frame.iloc[0].crop_pixel_count, 30)
        self.assertEqual(frame.iloc[0].valid_pixel_count, 100)
        self.assertTrue(frame.signal_strength.eq("presence_only").all())
        self.assertTrue(frame.iloc[1:].dominant_crop.isna().all())

    def test_wss_dominant_component_surface_and_worst_month(self):
        rows = [{"mukey": "1", "cokey": "a", "comppct_r": 80, "drainagecl": "Poorly drained",
                 "tfact": 5, "chkey": "top", "hzdept_r": 0, "hzdepb_r": 5, "kwfact": .32,
                 "flodfreqcl": "None"},
                {"mukey": "1", "cokey": "a", "comppct_r": 80, "drainagecl": "Poorly drained",
                 "tfact": 5, "chkey": "deep", "hzdept_r": 5, "hzdepb_r": 30, "kwfact": .4,
                 "flodfreqcl": "Frequent"},
                {"mukey": "1", "cokey": "b", "comppct_r": 20, "drainagecl": "Well drained",
                 "tfact": 3, "chkey": "other", "hzdept_r": 0, "hzdepb_r": 10, "kwfact": .1,
                 "flodfreqcl": "Very frequent"}]
        frame = spatial.wss_attributes(pd.DataFrame(rows))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0].drainage_class, "Poorly drained")
        self.assertEqual(frame.iloc[0].erosion_k_factor, .32)
        self.assertEqual(frame.iloc[0].flood_frequency, "Frequent")
        self.assertTrue(pd.isna(frame.iloc[0].soil_health_rating))

    def test_weights_apply_only_when_cdl_is_the_crop_signal(self):
        frame = pd.DataFrame({"dominant_crop": ["Corn", "Corn", None, "Corn"],
                              "signal_strength": ["presence_only", None, "presence_only", "presence_only"],
                              "crop_signal_source": [None, None, None, "trial"]})
        result = evidence.apply_crop_evidence(frame)
        self.assertEqual(result.sample_weight.tolist(), [.5, 1, 1, 1])
        self.assertEqual(result.iloc[3].crop_signal_source, "trial")
        self.assertEqual(result.iloc[3].signal_strength, "presence_only")

    def test_cdl_adds_only_positive_label_and_explicit_trial_overrides_it(self):
        frame = synthesize_samples(3, np.random.default_rng(3))
        frame["ph"] = 9.0
        baseline = model.attach_labels(frame)
        frame["dominant_crop"] = "Corn"
        frame["signal_strength"] = "presence_only"
        frame["crop_signal_source"] = ["cdl", "trial", "trial"]
        frame["observed_crop_Corn"] = [np.nan, 0, 1]
        labeled = model.attach_labels(frame)
        self.assertEqual(labeled.crop_Corn.tolist(), [1, 0, 1])
        for crop in T.CROP_NAMES:
            if crop != "Corn":
                self.assertEqual(labeled["crop_" + crop].tolist(), baseline["crop_" + crop].tolist())
        self.assertEqual(labeled.sample_weight.tolist(), [.5, 1, 1])

    def test_total_n_and_cec_never_fill_available_n_or_k(self):
        base = pd.DataFrame({"source": ["ssurgo"], "sample_id": ["ssurgo:1"], "mukey": ["1"],
                             "ph": [6.5], "ec": [1.], "nitrogen": [30.], "phosphorus": [np.nan],
                             "potassium": [np.nan], "n_derived": [True], "n_method": ["OM_proxy"]})
        grid = pd.DataFrame({"sample_id": ["sg:1"], "mukey": ["1"], "ph": [6.5],
                             "organic_carbon_pct": [2.], "soilgrids_total_nitrogen_ppm": [5000.],
                             "cec": [40.], "latitude": [29.5], "longitude": [-95.8],
                             "source_depth_cm": ["0-5"], "record_kind": ["model_prediction"]})
        crops = pd.DataFrame({"mukey": ["1"], "dominant_crop": ["Corn"], "signal_strength": ["presence_only"]})
        result = spatial.enrich(base, grid, crops, pd.DataFrame(columns=spatial.WSS_COLUMNS))
        self.assertTrue(result.potassium.isna().all())
        self.assertAlmostEqual(result.iloc[1].nitrogen, 2 * 1.724 * 15)
        self.assertEqual(result.iloc[1].soilgrids_total_nitrogen_ppm, 5000)
        self.assertTrue(result.n_derived.all())
        self.assertTrue(result.signal_strength.eq("presence_only").all())
        self.assertTrue(result.sample_weight.eq(.5).all())
        with self.assertRaisesRegex(ValueError, "one row per mukey"):
            spatial.enrich(base, grid, pd.concat([crops, crops]), pd.DataFrame(columns=spatial.WSS_COLUMNS))

    def test_lab_depth_overlap_keeps_measured_nutrients_and_records_grid_ids(self):
        base = pd.DataFrame({"source": ["kssl", "kssl"], "sample_id": ["lab:1", "lab:2"],
                             "mukey": ["1", "1"], "latitude": [29.5, 29.5], "longitude": [-95.8, -95.8],
                             "hzn_top": [0, 10], "hzn_bot": [10, 30],
                             "ph": [6.5, 6.5], "ec": [1., 1.], "nitrogen": [17., 17.],
                             "phosphorus": [20., 20.], "potassium": [150., 150.],
                             "n_derived": [False, False], "n_method": ["measured_KCl_NO3-N"] * 2})
        grid = pd.DataFrame({"sample_id": ["sg:top", "sg:lower"], "mukey": ["1", "1"],
                             "ph": [6., 7.], "organic_carbon_pct": [2., 1.],
                             "soilgrids_total_nitrogen_ppm": [4000., 2000.],
                             "cec": [40., 30.], "bulk_density_g_cm3": [1.3, 1.4],
                             "latitude": [29.5, 29.5], "longitude": [-95.8, -95.8],
                             "source_depth_cm": ["0-5", "5-15"], "record_kind": ["model_prediction"] * 2})
        result = spatial.enrich(base, grid, pd.DataFrame(columns=spatial.CDL_COLUMNS),
                                pd.DataFrame(columns=spatial.WSS_COLUMNS))
        self.assertEqual(result.iloc[0].soilgrids_total_nitrogen_ppm, 3000)
        self.assertEqual(result.iloc[0].soilgrids_sample_id, "sg:top;sg:lower")
        self.assertTrue(pd.isna(result.iloc[1].soilgrids_sample_id))
        self.assertEqual(result.iloc[0].nitrogen, 17)
        self.assertEqual(result.iloc[0].potassium, 150)
        self.assertFalse(result.iloc[0].n_derived)

    def test_all_three_estimators_receive_matching_weights(self):
        # Small actual estimators exercise sklearn/XGBoost sample_weight support.
        frame = synthesize_samples(100, np.random.default_rng(42))
        frame.loc[::2, "dominant_crop"] = "Corn"
        frame.loc[::2, "signal_strength"] = "presence_only"
        labeled = model.attach_labels(frame)
        original_reg, original_cls, original_rf = model.XGBRegressor, model.XGBClassifier, model.RandomForestClassifier
        fits = []
        def tiny(cls):
            def build(**kwargs):
                kwargs["n_estimators"] = 2
                obj = cls(**kwargs)
                original_fit = obj.fit
                def fit(X, y, **kw):
                    fits.append(np.asarray(kw["sample_weight"]))
                    return original_fit(X, y, **kw)
                # MultiOutputClassifier clones its estimator, so check its fit below.
                if cls is not original_rf:
                    obj.fit = fit
                return obj
            return build
        with mock.patch.object(model, "XGBRegressor", side_effect=tiny(original_reg)), \
             mock.patch.object(model, "XGBClassifier", side_effect=tiny(original_cls)), \
             mock.patch.object(model, "RandomForestClassifier", side_effect=tiny(original_rf)), \
             mock.patch.object(model.MultiOutputClassifier, "fit", autospec=True,
                               wraps=None) as crop_fit:
            def fit_crops(self, X, y, sample_weight):
                fits.append(np.asarray(sample_weight))
                self.predict = lambda X: np.zeros((len(X), len(T.CROP_NAMES)), dtype=int)
                return self
            crop_fit.side_effect = fit_crops
            bundle = model.train_models(labeled, verbose=False)
        self.assertEqual(len(fits), 3)
        for weights in fits:
            self.assertEqual(len(weights), 80)
            self.assertEqual(set(weights), {.5, 1.})
            np.testing.assert_array_equal(weights, fits[0])
        self.assertEqual(bundle["metadata"]["sample_weight_counts"], {"0.5": 50, "1.0": 50})

    def test_mlra_lookup_is_derived_and_rejects_unexpected_symbols(self):
        """The MLRA comes from laoverlap, and symbols are never inlined blindly."""
        lookup = spatial.mlra_for_area_sql("TX157")
        self.assertIn("laoverlap", lookup)
        self.assertIn("areatypename = 'MLRA'", lookup)
        self.assertIn("'TX157'", lookup)
        for builder in (spatial.mlra_areas_sql, spatial.mlra_ssurgo_sql, spatial.mlra_boundary_sql):
            sql = builder(["150A"])
            self.assertIn("'150A'", sql)
            # The STATSGO2 national legend overlaps every MLRA and is not a
            # survey area, so it must never enter a regional pull.
            self.assertIn("l.areasymbol <> 'US'", sql)
            with self.assertRaisesRegex(ValueError, "unexpected symbol"):
                builder(["150A'; DROP TABLE legend--"])
            with self.assertRaisesRegex(ValueError, "At least one symbol"):
                builder([])
        # The saved run recorded the derived answer rather than a hardcoded one.
        provenance = json.loads((ROOT / "data" / "real" / "provenance.json").read_text())
        self.assertEqual(provenance["mlra"]["mlra_symbols"], ["150A"])
        self.assertEqual(provenance["mlra"]["mlra_names"]["150A"], "Gulf Coast Prairies")
        self.assertEqual(provenance["mlra"]["survey_area_count"], 40)
        self.assertIn("TX157", provenance["mlra"]["survey_areas"])
        self.assertNotIn("US", provenance["mlra"]["survey_areas"])

    def test_soilgrids_point_sampling_keeps_one_row_per_native_cell(self):
        """Pedons sharing a 250 m cell get one prediction, not one row each."""
        units = {"phh2o": {"mapped_units": "pH*10", "d_factor": 10},
                 "soc": {"mapped_units": "dg/kg", "d_factor": 10},
                 "nitrogen": {"mapped_units": "cg/kg", "d_factor": 100},
                 "cec": {"mapped_units": "mmol(c)/kg", "d_factor": 10},
                 "bdod": {"mapped_units": "cg/cm\u00b3", "d_factor": 100}}
        bounds = {"south": 29.3, "north": 29.9, "west": -96.3, "east": -95.4}
        _, _, shape = spatial.soilgrids_window(bounds)
        coverage = {"phh2o": 65., "soc": 200., "nitrogen": 150., "cec": 250., "bdod": 130.}

        def fake(cache, prop, depth, box, refresh=False, stem=None):
            return np.full(shape, coverage[prop], dtype=float)

        # Two coordinates a few metres apart share a cell; the third does not.
        lon = [-95.8, -95.80001, -95.6]
        lat = [29.5, 29.50001, 29.7]
        with mock.patch.object(spatial, "soilgrids_coverage", side_effect=fake):
            frame, status = spatial.soilgrids_at_points(
                Path("/nonexistent"), bounds, lon, lat, units, prefix="soilgrids_gulf")
        self.assertEqual(status["pedon_cells"], 2)
        self.assertEqual(len(frame), 2 * len(spatial.DEPTHS))
        self.assertTrue(frame.sample_id.is_unique)
        self.assertTrue(frame.sample_id.str.startswith("soilgrids_gulf:").all())
        self.assertTrue((frame.ph == 6.5).all())
        self.assertTrue((frame.soilgrids_total_nitrogen_ppm == 1500).all())
        self.assertEqual(set(frame.source_depth_cm), set(spatial.DEPTHS))
        # A point outside the window contributes nothing rather than clamping.
        with mock.patch.object(spatial, "soilgrids_coverage", side_effect=fake):
            empty, status = spatial.soilgrids_at_points(
                Path("/nonexistent"), bounds, [0.0], [0.0], units)
        self.assertTrue(empty.empty)
        self.assertEqual(status["status"], "skipped")

    def test_saved_cdl_flags_survive_final_join(self):
        directory = ROOT / "data" / "real"
        cdl = pd.read_csv(directory / "cdl_crop_by_mukey.csv")
        final = pd.read_csv(directory / "fort_bend_real_training.csv", low_memory=False)
        self.assertEqual(len(cdl), 51)
        self.assertTrue(cdl.signal_strength.eq("presence_only").all())
        joined = final.loc[final.cdl_year.notna()]
        self.assertTrue(joined.signal_strength.eq("presence_only").all())
        self.assertTrue(joined.loc[joined.dominant_crop.notna(), "sample_weight"].eq(.5).all())
        self.assertEqual(set(final.loc[final.source.eq("soilgrids"), "source_depth_cm"]), {"0-5", "5-15"})
        summary = json.loads((directory / "summary.json").read_text())
        self.assertEqual(summary["nitrogen_source_breakdown"]["measured"], 0)
        self.assertEqual(summary["spatial_sources"]["cdl"]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
