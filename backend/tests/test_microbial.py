"""Microbial activity: the optional soil-respiration lab value and its rating.

One lab value for the whole field, the 24-hour CO2 burst after dried soil is
rewetted (ppm CO2-C), may be sent with /api/submit. microbial rates it against
the published Ward Laboratories Haney Test bands kept in
backend/data/soil_respiration_bands.json, and the analysis stores the answer as
`microbial`. The Soil Profile page of the combined PDF (and the results page)
show the rating; without a value the analysis has no `microbial` key and both
say the in-house test is in development.

Display only: the health score, crop assessment and recommendations are the
same with or without a respiration value. Nothing here reaches the network:
urlopen, socket connections and the two soil-survey fetchers are replaced for
the whole module, and a test that trips them fails.
"""

import base64
import copy
import importlib
import importlib.util
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app, config, database  # noqa: E402
from backend import microbial, model_runner, pdf_report, soil_info, soil_report  # noqa: E402

from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402


# ---------------------------------------------------------------------------
# The published table
# ---------------------------------------------------------------------------
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANDS_NAME = "soil_respiration_bands.json"
BANDS_FILE = os.path.join(BACKEND_DIR, "data", BANDS_NAME)

UNITS = "ppm CO2-C"
METRIC = "Soil respiration (24-hour CO2 burst)"
NOT_PROVIDED = "Microbial activity: in-house test in development"
UNRATED = "Microbial activity rating is unavailable right now."
RANGE_ERROR = "soil_respiration must be a number from 0 to 5000 (ppm CO2-C)"
GUIDE_URL = ("https://www.wardlab.com/wp-content/uploads/2020/02/"
             "Haney-Rev-1.0-Interpretation-Guide-PDF-1.pdf")
SOURCE = {
    "title": "Haney Test Interpretation Guide v1.0, Soil Respiration Ranking Table",
    "publisher": "Ward Laboratories, Inc., Kearney, Nebraska",
    "year": 2019,
    "url": GUIDE_URL,
}
NOT_CALIBRATED = "A published lab guideline, not calibrated for Texas soils."

# Ward Laboratories, Haney Test Interpretation Guide v1.0 (2019), Soil
# Respiration Ranking Table, with the "Implications" column shortened. These
# are the only numbers the feature may use; change them only from a source.
PUBLISHED_BANDS = [
    ("Very Low", 0, 10, "Very little potential for microbial activity; slow nutrient "
                        "cycling and residue decomposition."),
    ("Low", 11, 20, "Minimal potential for nutrient cycling."),
    ("Below Average", 21, 30, "Some potential for nutrient cycling."),
    ("Slightly Below Average", 31, 50, "Low to moderate potential for microbial activity."),
    ("Slightly Above Average", 51, 70, "Moderate potential for microbial activity."),
    ("Above Average", 71, 100, "Good potential for microbial activity."),
    ("High", 101, 200, "High potential for microbial activity."),
    ("Very High", 201, None, "High to very high potential for microbial activity and "
                             "nitrogen mineralization."),
]
LABELS = [label for label, _, _, _ in PUBLISHED_BANDS]
RANGE_TEXTS = ["0-10", "11-20", "21-30", "31-50", "51-70", "71-100", "101-200",
               "above 200"]


def shipped():
    """The shipped bands file, parsed (a fresh copy each time)."""
    with open(BANDS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Pointing microbial at another config
# ---------------------------------------------------------------------------
def point_microbial_at(test, path):
    """microbial reads `path`, from an empty cache, until the test ends.

    Reloading the module drops whatever config it has cached (and its lock)
    without depending on what the cache is called; app.py holds the module
    object, so it sees the same functions. The last cleanup reloads it again,
    back on the shipped file.
    """
    importlib.reload(microbial)
    patcher = mock.patch.object(microbial, "BANDS_PATH", path)
    patcher.start()
    test.addCleanup(importlib.reload, microbial)
    test.addCleanup(patcher.stop)


class OpenSpy:
    """Records every opening of a bands file through open() or io.open()."""

    def __init__(self, test, refuse=False):
        self.opened = []
        self._lock = threading.Lock()
        real_open = io.open

        def spy(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                name = os.fsdecode(os.fspath(file))
                if BANDS_NAME in name:
                    with self._lock:
                        self.opened.append(name)
                    if refuse:
                        raise AssertionError("the bands file was read at import time")
            return real_open(file, *args, **kwargs)

        # pathlib's read_text goes through io.open, plain open() through builtins.
        for target in ("builtins.open", "io.open"):
            patcher = mock.patch(target, new=spy)
            patcher.start()
            test.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# PDF text
# ---------------------------------------------------------------------------
def flow_text(flowables):
    """Plain text of ReportLab flowables, including paragraphs inside tables."""
    out = []
    for item in flowables:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, (list, tuple)):
            out.append(flow_text(item))
        elif hasattr(item, "getPlainText"):
            out.append(item.getPlainText())
        elif hasattr(item, "_cellvalues"):
            out.append(flow_text([cell for row in item._cellvalues for cell in row]))
        elif hasattr(item, "_content"):  # KeepTogether and friends
            out.append(flow_text(item._content))
    return "\n".join(out)


def paragraphs(flowables):
    """Every Paragraph in the flowables, tables and KeepTogethers included."""
    for item in flowables:
        if isinstance(item, (list, tuple)):
            yield from paragraphs(item)
        elif hasattr(item, "getPlainText"):
            yield item
        elif hasattr(item, "_cellvalues"):
            yield from paragraphs([cell for row in item._cellvalues for cell in row])
        elif hasattr(item, "_content"):
            yield from paragraphs(item._content)


def derives_from(style, base):
    """True when `style` is `base` or a ParagraphStyle built on it."""
    while style is not None:
        if style.name == base.name:
            return True
        style = getattr(style, "parent", None)
    return False


_PDF_STRING = re.compile(rb"\((?:\\.|[^\\)])*\)", re.S)


def pdf_text(pdf_bytes):
    """Text drawn on the pages of a ReportLab PDF, whitespace collapsed.

    ReportLab compresses page content (ASCII85 over Flate), so the words are
    not in the raw bytes and a plain `in` check on them would be misleading.
    """
    lines = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.S):
        data = match.group(1).strip()
        try:
            if data.endswith(b"~>"):
                data = base64.a85decode(data, adobe=True)
            data = zlib.decompress(data)
        except (ValueError, zlib.error):
            continue  # not a page content stream
        for block in re.findall(rb"\bBT\b(.*?)\bET\b", data, re.S):
            parts = [re.sub(rb"\\([()\\])", rb"\1", s[1:-1])
                     for s in _PDF_STRING.findall(block)]
            if parts:
                lines.append(b" ".join(parts).decode("latin-1"))
    return " ".join(" ".join(lines).split())


def page_count(pdf_bytes):
    # /Type /Page, but not the /Type /Pages node that lists them.
    return len(re.findall(rb"/Type\s*/Page(?![s])", pdf_bytes))


# ---------------------------------------------------------------------------
# Module guards: no network, and quiet expected warnings
# ---------------------------------------------------------------------------
NETWORK_ATTEMPTS = []


def _refuse_network(*args, **kwargs):
    NETWORK_ATTEMPTS.append(repr(args[:2]))
    raise RuntimeError("a microbial test tried to reach the network")


_GUARDS = []
_QUIET = logging.NullHandler()
_LOGGERS = ("backend.microbial", "backend.soil_info", "backend.soil_report")


def setUpModule():
    for target in ("urllib.request.urlopen", "socket.create_connection"):
        guard = mock.patch(target, side_effect=_refuse_network)
        guard.start()
        _GUARDS.append(guard)
    for name in ("_fetch_soilweb", "_fetch_sda"):
        guard = mock.patch.object(soil_info, name, side_effect=_refuse_network)
        guard.start()
        _GUARDS.append(guard)
    # The broken-config cases log on purpose; tests that care use assertLogs.
    for name in _LOGGERS:
        logging.getLogger(name).addHandler(_QUIET)


def tearDownModule():
    while _GUARDS:
        _GUARDS.pop().stop()
    for name in _LOGGERS:
        logging.getLogger(name).removeHandler(_QUIET)


class MicrobialTestCase(unittest.TestCase):
    """SQLite test database, no network, and a scratch folder for configs."""

    def setUp(self):
        patcher = mock.patch.object(config, "DATABASE_URL", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._assert_no_network)
        self.scratch = tempfile.mkdtemp(prefix="ff-microbial-tests-")
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def _assert_no_network(self):
        attempts = list(NETWORK_ATTEMPTS)
        del NETWORK_ATTEMPTS[:]
        self.assertEqual(attempts, [], "a test reached for the network")

    def write_config(self, document, name=BANDS_NAME):
        """A config file in the scratch folder: JSON, or raw text if a str."""
        path = os.path.join(self.scratch, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(document if isinstance(document, str) else json.dumps(document))
        return path

    def use_placeholder_config(self):
        document = shipped()
        document["placeholder"] = True
        point_microbial_at(self, self.write_config(document))


# ---------------------------------------------------------------------------
# The shipped config
# ---------------------------------------------------------------------------
class ShippedConfigTests(MicrobialTestCase):
    def test_constants(self):
        self.assertEqual(microbial.MAX_RESPIRATION, 5000.0)
        self.assertEqual(microbial.NOT_PROVIDED_MESSAGE, NOT_PROVIDED)
        self.assertTrue(os.path.isabs(microbial.BANDS_PATH), microbial.BANDS_PATH)
        self.assertTrue(os.path.isfile(microbial.BANDS_PATH))
        self.assertTrue(os.path.samefile(microbial.BANDS_PATH, BANDS_FILE))

    def test_the_bands_are_the_published_ones(self):
        document = shipped()
        bands = document["bands"]
        self.assertEqual([(b["label"], b["min"], b["max"], b["meaning"]) for b in bands],
                         PUBLISHED_BANDS)
        self.assertEqual(len(bands), 8)
        self.assertEqual(bands[0]["min"], 0)
        self.assertIsNone(bands[-1]["max"])
        maxes = [b["max"] for b in bands[:-1]]
        self.assertEqual(maxes, sorted(maxes))
        self.assertEqual(len(set(maxes)), len(maxes), "two bands share a max")
        for band in bands:
            for key in ("min", "max"):
                value = band[key]
                self.assertTrue(value is None or type(value) is int,
                                "%s %s should be a whole number, got %r"
                                % (band["label"], key, value))

    def test_it_cites_its_source_and_is_not_a_placeholder(self):
        document = shipped()
        self.assertIs(document["placeholder"], False)
        self.assertEqual(document["confidence"], "medium")
        self.assertEqual(document["metric"], METRIC)
        self.assertEqual(document["units"], UNITS)
        self.assertEqual(document["units_long"],
                         "mg of CO2-C released per kg of dried soil in the 24 hours "
                         "after it is rewetted")
        self.assertEqual({key: document["source"][key] for key in SOURCE}, SOURCE)
        self.assertIn("Table 66", document["source"]["also_in"])
        self.assertIn("not field-calibrated", document["basis"])
        self.assertIn("not specific to Texas soils", document["basis"])
        for name in ("Texas A&M AgriLife", "Noble Research Institute"):
            self.assertIn(name, document["corroboration"])

    def test_the_caveats_say_where_the_scale_is_soft(self):
        caveats = shipped()["caveats"]
        self.assertEqual(len(caveats), 3)
        for caveat in caveats:
            self.assertIsInstance(caveat, str)
            self.assertTrue(caveat.strip())
        joined = " ".join(caveats)
        for phrase in ("sliding scale", "below 10", "above 200", "Low 0-60",
                       "Marginal 60-120", "Optimum above 120", "pH 7.5"):
            self.assertIn(phrase, joined)
        self.assertIn("first band whose max it does not exceed", shipped()["band_rule"])

    def test_load_config_returns_the_shipped_file(self):
        point_microbial_at(self, BANDS_FILE)
        loaded = microbial.load_config()
        document = shipped()
        self.assertEqual([b["label"] for b in loaded["bands"]], LABELS)
        self.assertEqual([(b["min"], b["max"]) for b in loaded["bands"]],
                         [(b["min"], b["max"]) for b in document["bands"]])
        self.assertIs(loaded["placeholder"], False)
        self.assertEqual(loaded["source"]["url"], GUIDE_URL)


# ---------------------------------------------------------------------------
# rate()
# ---------------------------------------------------------------------------
class RateTests(MicrobialTestCase):
    def test_band_edges(self):
        # A value goes to the first band whose max it does not exceed, so the
        # gap between two whole-number ranges (10.5) belongs to the higher one.
        cases = [(0, "Very Low"), (10, "Very Low"), (10.5, "Low"), (11, "Low"),
                 (30, "Below Average"), (30.5, "Slightly Below Average"),
                 (50, "Slightly Below Average"), (70, "Slightly Above Average"),
                 (100, "Above Average"), (150, "High"), (200, "High"),
                 (200.1, "Very High"), (201, "Very High"), (1000, "Very High"),
                 (5000, "Very High")]
        for value, label in cases:
            with self.subTest(value=value):
                payload = microbial.rate(float(value))
                self.assertEqual(payload["status"], "rated")
                self.assertEqual(payload["band"], label)
                self.assertEqual(payload["band_index"], LABELS.index(label))
                self.assertEqual(payload["band_count"], 8)

    def test_every_band_names_its_range(self):
        for index, value in enumerate((5, 15, 25, 40, 60, 80, 150, 300)):
            label, low, high, meaning = PUBLISHED_BANDS[index]
            with self.subTest(band=label):
                payload = microbial.rate(float(value))
                self.assertEqual(payload["band"], label)
                self.assertEqual(payload["band_index"], index)
                self.assertEqual(payload["range_text"], RANGE_TEXTS[index])
                self.assertEqual(payload["band_min"], low)
                self.assertEqual(payload["band_max"], high)
                self.assertEqual(payload["meaning"], meaning)

    def test_the_payload_for_45(self):
        document = shipped()
        payload = microbial.rate(45.0)
        json.dumps(payload, allow_nan=False)
        expected = {
            "status": "rated", "value": 45.0, "units": UNITS, "metric": METRIC,
            "band": "Slightly Below Average", "band_index": 3, "band_count": 8,
            "band_min": 31, "band_max": 50, "range_text": "31-50",
            "meaning": PUBLISHED_BANDS[3][3], "placeholder": False,
            "confidence": "medium", "basis": document["basis"],
            "caveats": document["caveats"],
        }
        self.assertEqual({key: payload[key] for key in expected}, expected)
        self.assertEqual({key: payload["source"][key] for key in SOURCE}, SOURCE)
        self.assertEqual([{key: band[key] for key in ("label", "min", "max")}
                          for band in payload["bands"]],
                         [{"label": label, "min": low, "max": high}
                          for label, low, high, _ in PUBLISHED_BANDS])

    def test_the_value_rounds_half_up_to_one_decimal(self):
        # Float rounding gives 45.2 for 45.25 and 1.2 for 1.25 (both are exact
        # binary fractions, so round() goes to the even digit).
        cases = [(45.25, 45.3), (1.25, 1.3), (0.05, 0.1), (45.0, 45.0),
                 (45.24, 45.2), (199.94, 199.9), (1000.0, 1000.0)]
        for value, shown in cases:
            with self.subTest(value=value):
                payload = microbial.rate(value)
                self.assertEqual(payload["value"], shown)
                self.assertIsInstance(payload["value"], float)

    def test_the_value_shown_and_its_band_agree(self):
        # 10.04 is printed as 10.0, so calling it Low (11-20) would contradict
        # the number beside it; likewise 200.04 shown as 200.0 is still High.
        for value in (10.04, 10.05, 20.04, 50.04, 200.04, 200.05):
            with self.subTest(value=value):
                payload = microbial.rate(value)
                shown = microbial.rate(payload["value"])
                self.assertEqual(payload["band"], shown["band"])
        self.assertEqual(microbial.rate(10.04)["band"], "Very Low")
        self.assertEqual(microbial.rate(200.04)["band"], "High")

    def test_every_band_is_json_safe(self):
        for value in (0.0, 10.5, 45.0, 200.1, 5000.0):
            with self.subTest(value=value):
                json.dumps(microbial.rate(value), allow_nan=False)


# ---------------------------------------------------------------------------
# parse_respiration()
# ---------------------------------------------------------------------------
class ParseRespirationTests(MicrobialTestCase):
    def test_blank_means_not_provided(self):
        for value in (None, "", "  ", "\t\n"):
            with self.subTest(value=value):
                self.assertIsNone(microbial.parse_respiration(value))

    def test_numbers_and_numeric_strings_become_floats(self):
        cases = [("45", 45.0), (45, 45.0), (45.0, 45.0), (" 45 ", 45.0),
                 ("45.5", 45.5), (0, 0.0), ("0", 0.0), (5000, 5000.0),
                 ("5000", 5000.0), ("1e2", 100.0)]
        for given, expected in cases:
            with self.subTest(given=given):
                parsed = microbial.parse_respiration(given)
                self.assertEqual(parsed, expected)
                self.assertIsInstance(parsed, float)

    def test_out_of_range_and_non_finite_values_raise_the_range_message(self):
        for value in (-1, "-1", -0.001, 5001, "5000.1", float("nan"), "nan",
                      float("inf"), "inf", "-inf", "1e999"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    microbial.parse_respiration(value)
                self.assertEqual(str(caught.exception), RANGE_ERROR)

    def test_other_bad_values_raise_value_error_naming_the_field(self):
        # Anything but ValueError would turn a bad request into a 500.
        # float() alone would read "4_5" as 45 and the Arabic-Indic "١٢" as 12.
        for value in ("abc", "45 ppm", "4,5", "4_5", "١٢", "0x10", True, False,
                      [45], {"value": 45}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    microbial.parse_respiration(value)
                self.assertIn("soil_respiration", str(caught.exception))


# ---------------------------------------------------------------------------
# load_config(): placeholder, broken, read once, lazy
# ---------------------------------------------------------------------------
def _broken_configs():
    """label -> a config document (or raw text) that must not load."""

    def edited(change):
        document = shipped()
        change(document)
        return document

    def set_band(index, key, value):
        return lambda d: d["bands"][index].__setitem__(key, value)

    return {
        "not JSON": "{oops",
        "a JSON array": [],
        "no bands": edited(lambda d: d.pop("bands")),
        "empty bands": edited(lambda d: d.__setitem__("bands", [])),
        "first min not 0": edited(set_band(0, "min", 1)),
        "maxes out of order": edited(set_band(2, "max", 15)),
        "a null max before the last band": edited(set_band(3, "max", None)),
        "a last band with a max": edited(set_band(7, "max", 1000)),
        "a blank label": edited(set_band(1, "label", "  ")),
        "no source title": edited(lambda d: d["source"].pop("title")),
        "a blank source url": edited(lambda d: d["source"].__setitem__("url", "")),
    }


class ConfigTests(MicrobialTestCase):
    def test_a_placeholder_config_marks_every_label(self):
        self.use_placeholder_config()
        payload = microbial.rate(45.0)
        self.assertEqual(payload["status"], "rated")
        self.assertIs(payload["placeholder"], True)
        self.assertEqual(payload["band"], "PLACEHOLDER Slightly Below Average")
        self.assertEqual([band["label"] for band in payload["bands"]],
                         ["PLACEHOLDER " + label for label in LABELS])
        self.assertEqual(payload["range_text"], "31-50")

    def test_a_broken_config_raises_value_error(self):
        for label, document in _broken_configs().items():
            with self.subTest(config=label):
                point_microbial_at(self, self.write_config(document))
                with self.assertRaises(ValueError):
                    microbial.load_config()

    def test_a_broken_or_missing_config_leaves_the_value_unrated(self):
        cases = dict(_broken_configs())
        cases["missing file"] = None
        for label, document in cases.items():
            with self.subTest(config=label):
                path = (os.path.join(self.scratch, "missing", BANDS_NAME)
                        if document is None else self.write_config(document))
                point_microbial_at(self, path)
                with self.assertLogs(level="WARNING"):
                    payload = microbial.rate(45.0)
                self.assertEqual(payload["status"], "unrated")
                self.assertEqual(payload["value"], 45.0)
                self.assertEqual(payload["units"], UNITS)
                self.assertEqual(payload["message"], UNRATED)
                self.assertNotIn("band", payload)
                json.dumps(payload, allow_nan=False)

    def test_the_config_is_read_once_even_from_many_threads(self):
        point_microbial_at(self, self.write_config(shipped()))
        spy = OpenSpy(self)
        barrier = threading.Barrier(8, timeout=5)
        loaded, errors = [], []

        def load():
            barrier.wait()
            try:
                loaded.append(microbial.load_config())
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=load) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(loaded), 8)
        for config_doc in loaded:
            self.assertEqual(config_doc, loaded[0])
        self.assertEqual(len(spy.opened), 1, spy.opened)

        # Ratings after that come from the cache, not another read.
        for value in (5.0, 45.0, 300.0):
            self.assertEqual(microbial.rate(value)["status"], "rated")
        self.assertEqual(len(spy.opened), 1, spy.opened)

    def test_importing_microbial_does_not_read_the_config(self):
        spy = OpenSpy(self, refuse=True)
        spec = importlib.util.spec_from_file_location("backend._microbial_import_probe",
                                                      microbial.__file__)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
        self.assertEqual(spy.opened, [], "the bands file was read at import time")
        self.assertTrue(os.path.samefile(module.BANDS_PATH, BANDS_FILE))


# ---------------------------------------------------------------------------
# /api/submit
# ---------------------------------------------------------------------------
SECTION = dict(section_label="A1", crop_type="Corn", latitude=29.364, longitude=-95.865,
               ph=6.5, ec=1.0, nitrogen=10, phosphorus=25, potassium=180)


def predictions(sections, _prefix):
    # The ML stack (pandas/xgboost) stays out of the run, as in test_score.
    return [dict(ml_health_score=72.0, ml_category_from_score="Good",
                 ml_crop_suitable=[], ml_health_category="Good",
                 ml_crop_confidence={}) for _ in sections]


def forget_farm(farm_id):
    database.execute("DELETE FROM results WHERE farm_id = ?", (farm_id,))
    database.execute("DELETE FROM samples WHERE farm_id = ?", (farm_id,))
    database.execute("DELETE FROM farms WHERE id = ?", (farm_id,))


def farm_count():
    return database.query_one("SELECT COUNT(*) AS n FROM farms")["n"]


def stored_analysis(farm_id):
    row = database.query_one("SELECT result_json FROM farms WHERE id = ?", (farm_id,))
    return json.loads(row["result_json"])


def logged_in_client():
    client = app.test_client()
    with client.session_transaction() as session:
        session["authed"] = True
        session["admin_id"] = 1
    return client


class SubmitTests(MicrobialTestCase):
    def setUp(self):
        super().setUp()
        self.client = logged_in_client()
        patcher = mock.patch.object(model_runner, "_predict", side_effect=predictions)
        patcher.start()
        self.addCleanup(patcher.stop)

    def submit(self, **extra):
        body = {"farm_name": "Respiration field", "date": "2026-10-23",
                "sections": [dict(SECTION)]}
        body.update(extra)
        response = self.client.post("/api/submit", json=body)
        if response.status_code == 200:
            self.addCleanup(forget_farm, response.json["farm_id"])
        return response

    def test_a_value_is_rated_and_stored_with_the_analysis(self):
        for value in (45, "45", 45.0, " 45 "):
            with self.subTest(value=value):
                response = self.submit(soil_respiration=value)
                self.assertEqual(response.status_code, 200, response.json)
                for analysis in (response.json, stored_analysis(response.json["farm_id"])):
                    rated = analysis["microbial"]
                    self.assertEqual(rated["status"], "rated")
                    self.assertEqual(rated["band"], "Slightly Below Average")
                    self.assertEqual(rated["value"], 45.0)
                    self.assertEqual(rated["units"], UNITS)
                    self.assertEqual(rated["source"]["url"], GUIDE_URL)

    def test_without_a_value_the_analysis_has_no_microbial_key(self):
        cases = {"missing": {}, "null": {"soil_respiration": None},
                 "empty": {"soil_respiration": ""}, "blank": {"soil_respiration": "  "}}
        for label, extra in cases.items():
            with self.subTest(case=label):
                response = self.submit(**extra)
                self.assertEqual(response.status_code, 200, response.json)
                for analysis in (response.json, stored_analysis(response.json["farm_id"])):
                    self.assertNotIn("microbial", analysis)

    def test_a_bad_value_is_a_400_and_nothing_is_saved(self):
        for value in ("abc", -5, "-5", 5001, float("nan"), float("inf"), True,
                      [45], {"value": 45}):
            with self.subTest(value=value):
                before = farm_count()
                response = self.submit(soil_respiration=value)
                self.assertEqual(response.status_code, 400)
                error = response.json.get("error")
                self.assertIsInstance(error, str)
                self.assertIn("soil_respiration", error)
                self.assertEqual(farm_count(), before, "a farm row was written")
        self.assertEqual(self.submit(soil_respiration=-5).json["error"], RANGE_ERROR)

    def test_the_rating_does_not_change_the_scores(self):
        def scored(analysis):
            analysis = copy.deepcopy(analysis)
            analysis.pop("farm_id")
            analysis.pop("microbial", None)
            for result in analysis["results"]:
                result.pop("sample_id")
            return analysis

        plain = self.submit()
        self.assertEqual(plain.status_code, 200, plain.json)
        for value in (5, 45, 1000):
            with self.subTest(value=value):
                rated = self.submit(soil_respiration=value)
                self.assertEqual(rated.status_code, 200, rated.json)
                self.assertIn("microbial", rated.json)
                self.assertEqual(scored(rated.json), scored(plain.json))
                stored = database.query_one(
                    "SELECT mean_health_score FROM farms WHERE id = ?",
                    (rated.json["farm_id"],))
                self.assertEqual(stored["mean_health_score"],
                                 plain.json["field_summary"]["mean_health_score"])

    def test_it_goes_alongside_a_soil_point(self):
        response = self.submit(soil_respiration=250,
                               soil_point={"lat": 29.364, "lon": -95.865})
        self.assertEqual(response.status_code, 200, response.json)
        analysis = stored_analysis(response.json["farm_id"])
        self.assertEqual(analysis["soil_point"], {"lat": 29.364, "lon": -95.865})
        self.assertEqual(analysis["microbial"]["band"], "Very High")
        self.assertEqual(analysis["microbial"]["range_text"], "above 200")

    def test_a_broken_config_never_fails_the_assessment(self):
        point_microbial_at(self, self.write_config("{oops"))
        response = self.submit(soil_respiration=45)
        self.assertEqual(response.status_code, 200, response.json)
        for analysis in (response.json, stored_analysis(response.json["farm_id"])):
            self.assertEqual(analysis["microbial"]["status"], "unrated")
            self.assertEqual(analysis["microbial"]["message"], UNRATED)

    def test_requires_login(self):
        before = farm_count()
        response = app.test_client().post("/api/submit", json={
            "farm_name": "x", "date": "2026-10-23", "sections": [dict(SECTION)],
            "soil_respiration": 45})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(farm_count(), before)


# ---------------------------------------------------------------------------
# The Soil biology block of the Soil Profile page
# ---------------------------------------------------------------------------
ANALYSIS = {
    "farm_name": "Needville Test Field",
    "date": "2026-10-23",
    "field_summary": {
        "mean_health_score": 71, "health_category": "Good",
        "min_health_score": 71, "max_health_score": 71, "n_sections": 1,
        "dominant_problem_label": "Acidic soil (low pH)",
    },
    "results": [
        {"section_label": "A1", "health_score": 71, "health_category": "Good",
         "latitude": 29.364, "longitude": -95.865, "crop_type": "Corn",
         "inputs": {"ph": 6.1, "ec": 1.0, "nitrogen": 20,
                    "phosphorus": 25, "potassium": 180}},
    ],
}
SOIL_POINT = {"lat": 29.364, "lon": -95.865}
SOIL_PROFILE = {
    "status": "ok", "reason_code": None, "reason": None, "lat": 29.364, "lon": -95.865,
    "point_key": "29.3640,-95.8650", "series": "Lake Charles", "component_pct": 90,
    "component_kind": "Series", "map_unit": "Lake Charles clay, 0 to 1 percent slopes",
    "map_unit_symbol": "La", "survey_area": "Fort Bend County, Texas", "texture": "Clay",
    "sand_pct": 11.4, "silt_pct": 37.4, "clay_pct": 51.2, "cec": 48.6,
    "drainage": "Moderately well drained", "hydrologic_group": "D",
    "organic_matter_pct": 4.0, "organic_matter_low": 2.0, "organic_matter_high": 6.0,
    "layer_top_cm": 0, "layer_bottom_cm": 29,
    "sources": ["UC Davis SoilWeb", "USDA Soil Data Access"],
    "source_note": "Mapped soil survey data (USDA NRCS SSURGO). These are typical "
                   "values for this soil type, not a measurement of this field.",
    "fetched_at": "2026-10-23T18:10:00+00:00", "cached": False,
}
SURVEY_HEADLINE = "Typical organic matter for this soil type: 4.0%"
LAB_NOTE = "Send a sample to a soil lab to measure this field's actual organic matter."
AVAIL_WIDTH = letter[0] - 1.5 * inch  # what build_combined_pdf lays out in

RATED_LINE = ("Microbial activity (soil respiration, 24-hour CO2 burst): 45.0 "
              "ppm CO2-C")
RATING_LINE = "Rating: Slightly Below Average (31-50 ppm, step 4 of 8)"


def field(soil_point=False, microbial_payload=None, with_microbial=False):
    """The test analysis, with a soil point and profile and/or a rating."""
    analysis = copy.deepcopy(ANALYSIS)
    if soil_point:
        analysis["soil_point"] = dict(SOIL_POINT)
        analysis["soil_profile"] = copy.deepcopy(SOIL_PROFILE)
    if with_microbial or microbial_payload is not None:
        analysis["microbial"] = copy.deepcopy(microbial_payload)
    return analysis


class SoilBiologyPageTests(MicrobialTestCase):
    """soil_report.soil_profile_flowables on its own."""

    def flowables(self, analysis):
        flow = soil_report.soil_profile_flowables(analysis, pdf_report._styles(),
                                                  AVAIL_WIDTH)
        pdf_report._render(list(flow))  # it has to lay out, not just construct
        return flow

    def page_text(self, analysis):
        text = " ".join(flow_text(self.flowables(analysis)).split())
        self.assertNotIn("None", text)
        return text

    def test_a_rated_value_shows_its_band_scale_and_source(self):
        payload = microbial.rate(45.0)
        flow = self.flowables(field(soil_point=True, microbial_payload=payload))
        text = " ".join(flow_text(flow).split())
        for phrase in ("Soil biology", RATED_LINE, RATING_LINE, PUBLISHED_BANDS[3][3],
                       "Rating bands:", "Ward Laboratories",
                       "Haney Test Interpretation Guide v1.0", "(2019)", GUIDE_URL,
                       NOT_CALIBRATED):
            self.assertIn(phrase, text)
        for label in LABELS:
            self.assertIn(label, text)
        for caveat in shipped()["caveats"]:
            self.assertIn(" ".join(caveat.split()), text)
        self.assertNotIn("PLACEHOLDER", text)
        self.assertNotIn(NOT_PROVIDED, text)
        heading = [p for p in paragraphs(flow) if p.getPlainText().strip() == "Soil biology"]
        self.assertEqual(len(heading), 1, "one 'Soil biology' heading")
        self.assertTrue(derives_from(heading[0].style, pdf_report._styles()["h2"]))

    def test_the_biology_block_comes_after_the_survey_content(self):
        text = self.page_text(field(soil_point=True, microbial_payload=microbial.rate(45.0)))
        self.assertTrue(text.startswith("Soil Profile"), text[:80])
        for phrase in (SURVEY_HEADLINE, "Lake Charles (90% of map unit)",
                       "not a measurement of this field", LAB_NOTE):
            self.assertIn(phrase, text)
            self.assertLess(text.index(phrase), text.index("Soil biology"))

    def test_the_top_band_reads_above_200(self):
        text = self.page_text(field(soil_point=True, microbial_payload=microbial.rate(250.0)))
        self.assertIn("250.0 ppm CO2-C", text)
        self.assertIn("Rating: Very High (above 200 ppm, step 8 of 8)", text)

    def test_without_a_value_the_page_says_the_test_is_in_development(self):
        flow = self.flowables(field(soil_point=True))
        text = " ".join(flow_text(flow).split())
        self.assertIn("Soil biology", text)
        self.assertIn(NOT_PROVIDED, text)
        self.assertNotIn("Rating", text)
        self.assertNotIn("Ward Laboratories", text)
        (message,) = [p for p in paragraphs(flow)
                      if " ".join(p.getPlainText().split()) == NOT_PROVIDED]
        self.assertTrue(derives_from(message.style, pdf_report._styles()["muted"]))
        self.assertIn(SURVEY_HEADLINE, text)  # Goal A's content is untouched

    def test_an_unrated_value_shows_the_message(self):
        payload = {"status": "unrated", "value": 45.0, "units": UNITS,
                   "message": UNRATED}
        text = self.page_text(field(soil_point=True, microbial_payload=payload))
        self.assertIn("Soil biology", text)
        self.assertIn(UNRATED, text)
        self.assertNotIn("Rating:", text)
        self.assertNotIn(NOT_PROVIDED, text)

    def test_a_rating_without_a_soil_point_gets_a_page_of_its_own(self):
        text = self.page_text(field(microbial_payload=microbial.rate(45.0)))
        self.assertTrue(text.startswith("Soil Profile"), text[:80])
        self.assertIn("Soil biology", text)
        self.assertIn(RATING_LINE, text)
        for survey_phrase in ("had not loaded", "USDA soil survey at",
                              "Mapped soil survey data", "organic matter", LAB_NOTE):
            self.assertNotIn(survey_phrase, text)

    def test_neither_a_soil_point_nor_a_rating_means_no_page(self):
        styles = pdf_report._styles()
        for label, analysis in (("plain", field()),
                                ("null microbial", field(with_microbial=True)),
                                ("junk microbial", dict(field(), microbial="junk")),
                                ("list microbial", dict(field(), microbial=[45]))):
            with self.subTest(case=label):
                self.assertEqual(soil_report.soil_profile_flowables(
                    analysis, styles, AVAIL_WIDTH), [])

    def test_a_placeholder_rating_is_flagged(self):
        self.use_placeholder_config()
        payload = microbial.rate(45.0)
        for soil_point in (True, False):
            with self.subTest(soil_point=soil_point):
                text = self.page_text(field(soil_point=soil_point,
                                            microbial_payload=payload))
                self.assertIn("PLACEHOLDER thresholds: not from a published source", text)
                self.assertIn("PLACEHOLDER Slightly Below Average", text)
                # Not cited as the guideline it contradicts, nor given its caveats.
                self.assertIn("Rating bands: placeholder values, not taken from Ward "
                              "Laboratories, Haney Test Interpretation Guide v1.0 (2019).",
                              text)
                for phrase in (GUIDE_URL, NOT_CALIBRATED, "sliding scale", "pH 7.5"):
                    self.assertNotIn(phrase, text)

    def test_a_value_between_two_ranges_reads_from_the_band_below(self):
        # 10.5 is Low, whose published range (11-20) would seem to leave it out.
        text = self.page_text(field(soil_point=True, microbial_payload=microbial.rate(10.5)))
        self.assertIn("10.5 ppm CO2-C", text)
        self.assertIn("Rating: Low (above 10 to 20 ppm, step 2 of 8)", text)
        text = self.page_text(field(soil_point=True, microbial_payload=microbial.rate(200.5)))
        self.assertIn("Rating: Very High (above 200 ppm, step 8 of 8)", text)

    def test_the_biology_block_is_kept_on_one_page(self):
        # A long survey moves the whole block over, never just its last caveats.
        for soil_point in (True, False):
            with self.subTest(soil_point=soil_point):
                flow = self.flowables(field(soil_point=soil_point,
                                            microbial_payload=microbial.rate(45.0)))
                block = flow[-1]
                self.assertTrue(hasattr(block, "_content"), type(block).__name__)
                self.assertEqual(flow_text(block._content).split("\n")[0], "Soil biology")

    def test_markup_in_the_payload_is_printed_literally(self):
        hostile = microbial.rate(45.0)
        hostile.update(band="<b>Low & </i>", meaning='Good <font size="40">huge</font>',
                       range_text="31-50 <br/>", caveats=["A & B <br/> C", "</para>"],
                       basis="<unclosed", metric="CO2 <i>burst")
        hostile["source"] = dict(hostile["source"], title="<para>", publisher="Ward & Sons",
                                 url="https://example.org/?a=1&b=<2>")
        hostile["bands"] = [dict(band, label="<x> & %d" % i)
                            for i, band in enumerate(hostile["bands"])]
        text = self.page_text(field(soil_point=True, microbial_payload=hostile))
        for phrase in ("<b>Low & </i>", 'Good <font size="40">huge</font>',
                       "A & B <br/> C", "https://example.org/?a=1&b=<2>", "<x> & 3"):
            self.assertIn(phrase, text)

    def test_odd_payloads_never_break_the_report(self):
        odd = [{}, {"status": "rated"}, {"status": 7, "value": "lots", "bands": "x"},
               {"status": "rated", "value": None, "band": None, "band_index": "four",
                "band_count": 0, "bands": [None, 3], "caveats": "one", "source": "Ward"},
               {"status": "unrated", "message": None}, {"status": "who knows"}]
        for payload in odd:
            for soil_point in (True, False):
                with self.subTest(payload=payload, soil_point=soil_point):
                    analysis = field(soil_point=soil_point, microbial_payload=payload)
                    soil_report.soil_profile_flowables(analysis, pdf_report._styles(),
                                                       AVAIL_WIDTH)
                    pdf_report.build_combined_pdf(analysis)


class ReportRouteTests(MicrobialTestCase):
    """GET /api/report/<id>/all, which must not touch the network."""

    def setUp(self):
        super().setUp()
        self.client = logged_in_client()

    def add_farm(self, analysis):
        farm_id = database.execute(
            "INSERT INTO farms (farm_name, date, n_sections, mean_health_score,"
            " result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (analysis["farm_name"], analysis["date"], len(analysis["results"]), 71,
             json.dumps(analysis), database.now_iso()))
        self.addCleanup(database.execute, "DELETE FROM farms WHERE id = ?", (farm_id,))
        return farm_id

    def report(self, analysis):
        response = self.client.get("/api/report/%d/all" % self.add_farm(analysis))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        return response.data

    def test_soil_point_and_rating(self):
        text = pdf_text(self.report(field(soil_point=True,
                                          microbial_payload=microbial.rate(45.0))))
        for phrase in ("Soil Profile", "Typical organic matter for this soil type",
                       "Soil biology", "Slightly Below Average", "Ward Laboratories",
                       "step 4 of 8"):
            self.assertIn(phrase, text)

    def test_soil_point_without_a_rating(self):
        text = pdf_text(self.report(field(soil_point=True)))
        self.assertIn("Soil Profile", text)
        self.assertIn(NOT_PROVIDED, text)
        self.assertNotIn("Ward Laboratories", text)

    def test_a_rating_without_a_soil_point_adds_one_page(self):
        plain = self.report(field())
        rated = self.report(field(microbial_payload=microbial.rate(45.0)))
        text = pdf_text(rated)
        for phrase in ("Soil Profile", "Soil biology", "Slightly Below Average"):
            self.assertIn(phrase, text)
        self.assertNotIn("had not loaded", text)
        self.assertEqual(page_count(rated), page_count(plain) + 1)

    def test_neither_keeps_the_old_page_count(self):
        plain = self.report(field())
        with_point = self.report(field(soil_point=True))
        with_both = self.report(field(soil_point=True,
                                      microbial_payload=microbial.rate(45.0)))
        text = pdf_text(plain)
        self.assertNotIn("Soil Profile", text)
        self.assertNotIn("Microbial activity", text)
        self.assertNotIn("Soil biology", text)
        # Cover and one section page, as before the soil features existed.
        self.assertEqual(page_count(plain), page_count(pdf_report.build_combined_pdf(
            copy.deepcopy(ANALYSIS))))
        self.assertEqual(page_count(with_point), page_count(plain) + 1)
        self.assertEqual(page_count(with_both), page_count(plain) + 1)

    def test_a_placeholder_rating_says_so(self):
        self.use_placeholder_config()
        text = pdf_text(self.report(field(soil_point=True,
                                          microbial_payload=microbial.rate(45.0))))
        self.assertIn("PLACEHOLDER", text)

    def test_an_unrated_value_says_the_rating_is_unavailable(self):
        payload = {"status": "unrated", "value": 45.0, "units": UNITS, "message": UNRATED}
        text = pdf_text(self.report(field(microbial_payload=payload)))
        self.assertIn(UNRATED, text)

    def test_a_submitted_value_reaches_the_pdf(self):
        with mock.patch.object(model_runner, "_predict", side_effect=predictions):
            response = self.client.post("/api/submit", json={
                "farm_name": "Respiration field", "date": "2026-10-23",
                "sections": [dict(SECTION)], "soil_respiration": "45"})
        self.assertEqual(response.status_code, 200, response.json)
        farm_id = response.json["farm_id"]
        self.addCleanup(forget_farm, farm_id)
        report = self.client.get("/api/report/%d/all" % farm_id)
        self.assertEqual(report.status_code, 200)
        text = pdf_text(report.data)
        self.assertIn("Soil biology", text)
        self.assertIn("Slightly Below Average", text)

    def test_the_single_section_report_has_no_biology(self):
        farm_id = self.add_farm(field(soil_point=True,
                                      microbial_payload=microbial.rate(45.0)))
        response = self.client.get("/api/report/%d/section/A1" % farm_id)
        self.assertEqual(response.status_code, 200)
        text = pdf_text(response.data)
        self.assertNotIn("Soil biology", text)
        self.assertNotIn("Microbial activity", text)


if __name__ == "__main__":
    unittest.main()
