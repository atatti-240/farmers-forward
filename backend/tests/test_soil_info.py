"""Soil Profile: the mapped USDA soil-survey lookup and where its answer shows up.

soil_info asks two public services about one point at the same time, UC Davis
SoilWeb for the soil series and USDA Soil Data Access (SDA) for organic matter
and soil properties, under one wall-clock deadline, and caches the answers SDA
gave definitively. The profile is display only: it is stored with a submission
and printed on the combined PDF, and never feeds the health score.

The bodies under fixtures/soil/ were recorded live on 2026-10-23 (the SDA ones
with the query in sda_query.sql):

    soilweb_fb1 / sda_fb1             29.3640, -95.8650  Lake Charles clay, Needville
    soilweb_fb2 / sda_fb2             29.6260, -95.9470  Bernard-Edna complex
    soilweb_empty / sda_water1_...    28.8000, -94.9000  Gulf of Mexico, no map unit
    sda_water2_water                  29.4910, -95.6483  Smithers Lake, a "Water" unit
    sda_urban1                        29.7604, -95.3698  downtown Houston, "Urban land"

Nothing here reaches the network: urlopen and socket connections are replaced
for the whole module by a recorder, and a test that trips it fails.
"""

import base64
import copy
import importlib.util
import io
import json
import logging
import os
import re
import socket
import sqlite3
import string
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from http.client import HTTPMessage
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app, config, database  # noqa: E402
from backend import model_runner, pdf_report, soil_info, soil_report  # noqa: E402

from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402


# ---------------------------------------------------------------------------
# Recorded responses
# ---------------------------------------------------------------------------
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "soil")


def load(name):
    """A recorded response body, parsed."""
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def sda_row(name, **changes):
    """The single row of a recorded SDA response, as column -> raw value."""
    header, values = load(name)["Table"]
    row = dict(zip(header, values))
    row.update(changes)
    return row


def sda_body(row):
    """A row back in SDA's JSON+COLUMNNAME response shape."""
    return {"Table": [list(row), list(row.values())]}


FB1 = (29.3640, -95.8650)       # Lake Charles clay
FB2 = (29.6260, -95.9470)       # Bernard-Edna complex
WATER1 = (28.8000, -94.9000)    # Gulf of Mexico: no map unit at all
WATER2 = (29.4910, -95.6483)    # Smithers Lake: a "Water" map unit
URBAN1 = (29.7604, -95.3698)    # downtown Houston: "Urban land"
KEY_FB1 = "29.3640,-95.8650"

SOILWEB, SDA = "UC Davis SoilWeb", "USDA Soil Data Access"

# Contract A's payload: the same keys whatever happened.
EXPECTED_KEYS = frozenset((
    "status", "reason_code", "reason", "lat", "lon", "point_key",
    "series", "component_pct", "component_kind", "map_unit", "map_unit_symbol",
    "survey_area", "texture", "sand_pct", "silt_pct", "clay_pct", "cec",
    "drainage", "hydrologic_group", "organic_matter_pct", "organic_matter_low",
    "organic_matter_high", "layer_top_cm", "layer_bottom_cm", "sources",
    "source_note", "fetched_at", "cached",
))
NUMERIC_KEYS = ("lat", "lon", "component_pct", "sand_pct", "silt_pct", "clay_pct",
                "cec", "organic_matter_pct", "organic_matter_low",
                "organic_matter_high", "layer_top_cm", "layer_bottom_cm")
INTEGER_KEYS = ("component_pct", "layer_top_cm", "layer_bottom_cm")
PROPERTY_KEYS = ("series", "component_pct", "texture", "sand_pct", "silt_pct",
                 "clay_pct", "cec", "drainage", "hydrologic_group",
                 "organic_matter_pct", "organic_matter_low", "organic_matter_high",
                 "layer_top_cm", "layer_bottom_cm")
STATUSES = {"ok", "partial", "no_data", "unavailable"}
REASON_CODES = {None, "no_survey_coverage", "non_soil_map_unit", "no_components",
                "om_not_in_survey", "properties_unavailable", "unavailable"}
# Words that do not belong in a sentence written for farmers.
JARGON = ("mukey", "cokey", "chkey", "SDA")

REASON_NO_MAP_UNIT = ("No soil survey map unit at this point (open water, or outside "
                      "USDA soil survey coverage).")
REASON_OM_NOT_RECORDED = ("The soil survey does not record organic matter for this "
                          "soil's surface layer.")
REASON_PARTIAL = ("The USDA soil survey service did not respond in time, so organic "
                  "matter and drainage are missing. Try again in a minute.")
REASON_NO_DATA = ("No soil survey data at this point (open water, urban land, or "
                  "outside survey coverage).")
REASON_UNAVAILABLE = ("The soil survey services did not respond in time. The "
                      "assessment is not affected; try again later.")

SOURCE_NOTE = ("Mapped soil survey data (USDA NRCS SSURGO; soil series via UC Davis "
               "SoilWeb, organic matter and soil properties via USDA Soil Data "
               "Access). These are typical values for this soil type, not a "
               "measurement of this field.")


def profile(**fields):
    """A soil_profile payload with every contract key, null unless given."""
    payload = dict.fromkeys(EXPECTED_KEYS)
    payload.update(lat=FB1[0], lon=FB1[1], point_key=KEY_FB1, sources=[],
                   source_note=SOURCE_NOTE, fetched_at="2026-10-23T18:10:00+00:00",
                   cached=False)
    payload.update(fields)
    return payload


# What FB1 must come out as; the fixtures are checked against it below, and the
# PDF tests use it so they do not depend on the lookup code.
PROFILE_FB1 = profile(
    status="ok", series="Lake Charles", component_pct=90, component_kind="Series",
    map_unit="Lake Charles clay, 0 to 1 percent slopes", map_unit_symbol="La",
    survey_area="Fort Bend County, Texas", texture="Clay", sand_pct=11.4,
    silt_pct=37.4, clay_pct=51.2, cec=48.6, drainage="Moderately well drained",
    hydrologic_group="D", organic_matter_pct=4.0, organic_matter_low=2.0,
    organic_matter_high=6.0, layer_top_cm=0, layer_bottom_cm=29,
    sources=[SOILWEB, SDA])


def comparable(payload):
    """The contract fields that do not vary from run to run."""
    return {key: payload.get(key) for key in EXPECTED_KEYS
            if key not in ("fetched_at", "cached", "source_note")}


def parse_utc(text):
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def assert_payload_shape(test, payload):
    """Contract A's invariants, which hold for every status."""
    test.assertTrue(EXPECTED_KEYS <= set(payload),
                    "payload is missing %s" % sorted(EXPECTED_KEYS - set(payload)))
    json.dumps(payload, allow_nan=False)
    test.assertIn(payload["status"], STATUSES)
    test.assertIn(payload["reason_code"], REASON_CODES)
    for key in NUMERIC_KEYS:
        value = payload[key]
        test.assertTrue(value is None or (isinstance(value, (int, float))
                                          and not isinstance(value, bool)),
                        "%s should be a number or null, got %r" % (key, value))
    for key in INTEGER_KEYS:
        test.assertTrue(payload[key] is None or type(payload[key]) is int,
                        "%s should be an int, got %r" % (key, payload[key]))
    test.assertIsInstance(payload["sources"], list)
    test.assertTrue(set(payload["sources"]) <= {SOILWEB, SDA}, payload["sources"])
    if payload["sources"]:
        test.assertIsInstance(payload["source_note"], str)
    test.assertIsInstance(payload["cached"], bool)
    test.assertEqual(payload["point_key"],
                     soil_info.point_key(payload["lat"], payload["lon"]))
    test.assertEqual(parse_utc(payload["fetched_at"]).utcoffset(), timedelta(0))

    reason = payload["reason"]
    if payload["status"] != "ok":
        test.assertIsNotNone(payload["reason_code"])
    if payload["reason_code"] is None:
        test.assertIsNone(reason)
    else:
        test.assertIsInstance(reason, str)
        test.assertTrue(reason.strip())
        for word in JARGON:
            test.assertIsNone(re.search(r"\b%s\b" % word, reason), reason)


# ---------------------------------------------------------------------------
# The cache table
# ---------------------------------------------------------------------------
def clear_cache():
    """Empty soil_info_cache, if soil_info has created it yet."""
    try:
        database.execute("DELETE FROM soil_info_cache")
    except sqlite3.OperationalError:  # no such table: nothing cached yet
        pass


def cache_row(key):
    try:
        return database.query_one(
            "SELECT point_key, payload, fetched_at FROM soil_info_cache "
            "WHERE point_key = ?", (key,))
    except sqlite3.OperationalError:
        return None


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


_PDF_STRING = re.compile(rb"\((?:\\.|[^\\)])*\)", re.S)


def pdf_text(pdf_bytes):
    """Text drawn on the pages of a ReportLab PDF, whitespace collapsed.

    ReportLab compresses page content (ASCII85 over Flate), so the words are
    not in the raw bytes; the document title is, which is why a plain `in`
    check on the bytes would be misleading.
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
# Stand-ins for the two services
# ---------------------------------------------------------------------------
HANG = object()  # a source that has not answered by the time the test ends


class FakeSources:
    """Replaces soil_info._fetch_soilweb and _fetch_sda for one test.

    Each answer is returned (a copy), raised when it is an exception, or for
    HANG held until the test ends, like a server that accepted the connection
    and then went quiet.
    """

    def __init__(self, test, soilweb, sda, delay=0.0):
        self.answers = {"soilweb": soilweb, "sda": sda}
        self.delay = delay
        self.calls = {"soilweb": [], "sda": []}
        # time.monotonic() as each fetch started and returned (or raised).
        self.started = {}
        self.finished = {}
        self._release = threading.Event()
        for source, name in (("soilweb", "_fetch_soilweb"), ("sda", "_fetch_sda")):
            patcher = mock.patch.object(soil_info, name, side_effect=self._fetcher(source))
            patcher.start()
            test.addCleanup(patcher.stop)
        test.addCleanup(self._release.set)

    def _fetcher(self, source):
        def fetch(lat, lon, timeout=None):
            self.started[source] = time.monotonic()
            try:
                return answer_with(lat, lon, timeout)
            finally:
                self.finished[source] = time.monotonic()

        def answer_with(lat, lon, timeout):
            self.calls[source].append((lat, lon, timeout))
            answer = self.answers[source]
            if answer is HANG:
                self._release.wait(5)
                raise socket.timeout("timed out")
            if self.delay:
                time.sleep(self.delay)
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)
        return fetch

    def count(self):
        return len(self.calls["soilweb"]) + len(self.calls["sda"])


def offline(test):
    """Fetchers that must not be called; check .count() afterwards."""
    return FakeSources(test, soilweb=AssertionError("unexpected SoilWeb fetch"),
                       sda=AssertionError("unexpected SDA fetch"))


def fb1_sources(test, **kwargs):
    return FakeSources(test, soilweb=load("soilweb_fb1.json"),
                       sda=sda_row("sda_fb1.json"), **kwargs)


class FakeHttp:
    """Replaces soil_info._http_json, answering each host with a recorded body.

    The real _fetch_soilweb, _fetch_sda, _parse_soilweb and _combine then run
    on the fixtures, which is as close to a live lookup as a test gets.
    """

    def __init__(self, test, soilweb, sda):
        self.answers = {"soilweb": soilweb, "sda": sda}
        self.requests = {"soilweb": [], "sda": []}
        patcher = mock.patch.object(soil_info, "_http_json", side_effect=self)
        patcher.start()
        test.addCleanup(patcher.stop)

    def __call__(self, url, data=None, timeout=None):
        host = urllib.parse.urlsplit(url).netloc.lower()
        if host == "casoilresource.lawr.ucdavis.edu":
            source = "soilweb"
        elif host == "sdmdataaccess.sc.egov.usda.gov":
            source = "sda"
        else:
            raise AssertionError("unexpected URL %r" % url)
        self.requests[source].append({"url": url, "data": data, "timeout": timeout})
        answer = self.answers[source]
        if isinstance(answer, BaseException):
            raise answer
        return copy.deepcopy(answer)


class FakeResponse:
    """Just enough of http.client.HTTPResponse for _http_json."""

    def __init__(self, body, status=200):
        self._body = io.BytesIO(body)
        self.status = status
        self.reads = []
        self.headers = HTTPMessage()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def read(self, amt=None):
        self.reads.append(amt)
        return self._body.read() if amt is None else self._body.read(amt)

    def getcode(self):
        return self.status

    def info(self):
        return self.headers

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StreamingResponse(FakeResponse):
    """A FakeResponse with read1, which is how a real HTTPResponse is read.

    Each read1 hands back at most `piece` bytes, after `pause` seconds, like a
    server that sends its reply a little at a time.
    """

    def __init__(self, body, piece=None, pause=0.0):
        super().__init__(body)
        self.piece = piece
        self.pause = pause

    def read1(self, amt=-1):
        self.reads.append(amt)
        if self.pause:
            time.sleep(self.pause)
        if self.piece is not None:
            amt = self.piece if amt is None or amt < 0 else min(amt, self.piece)
        return self._body.read(amt)


# ---------------------------------------------------------------------------
# Module guards: no network, and quiet expected warnings
# ---------------------------------------------------------------------------
NETWORK_ATTEMPTS = []


def _refuse_network(*args, **kwargs):
    NETWORK_ATTEMPTS.append(repr(args[:1]))
    raise RuntimeError("a soil test tried to reach the network")


_GUARDS = []
_QUIET = logging.NullHandler()
_LOGGERS = ("backend.soil_info", "backend.soil_report")


def setUpModule():
    for target in ("urllib.request.urlopen", "socket.create_connection"):
        guard = mock.patch(target, side_effect=_refuse_network)
        guard.start()
        _GUARDS.append(guard)
    # The DB-error and series-mismatch cases log warnings on purpose; tests
    # that care capture them with assertLogs.
    for name in _LOGGERS:
        logging.getLogger(name).addHandler(_QUIET)


def tearDownModule():
    while _GUARDS:
        _GUARDS.pop().stop()
    for name in _LOGGERS:
        logging.getLogger(name).removeHandler(_QUIET)


class SoilTestCase(unittest.TestCase):
    """SQLite test database, an empty soil cache, and no network."""

    def setUp(self):
        patcher = mock.patch.object(config, "DATABASE_URL", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        clear_cache()
        self.addCleanup(clear_cache)
        self.addCleanup(self._assert_no_network)

    def _assert_no_network(self):
        attempts = list(NETWORK_ATTEMPTS)
        del NETWORK_ATTEMPTS[:]
        self.assertEqual(attempts, [], "a test reached for the network")


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------
class ParseCoordsTests(SoilTestCase):
    def test_numbers_and_numeric_strings_are_accepted(self):
        cases = [(("29.364", "-95.865"), (29.364, -95.865)),
                 ((29, -95), (29.0, -95.0)),
                 ((29.364, -95.865), (29.364, -95.865)),
                 ((90, 180), (90.0, 180.0)),
                 ((-90, -180), (-90.0, -180.0)),
                 ((0, 0), (0.0, 0.0))]
        for given, expected in cases:
            with self.subTest(given=given):
                lat, lon = soil_info.parse_coords(*given)
                self.assertEqual((lat, lon), expected)
                self.assertIsInstance(lat, float)
                self.assertIsInstance(lon, float)

    def test_bad_coordinates_raise_value_error_with_a_message(self):
        cases = [(None, None), (None, -95.8), (29.3, None), ("", ""), ("  ", "1"),
                 ("abc", "-95.8"), ("29.3", "west"), (float("nan"), 0), ("nan", "0"),
                 (0, float("nan")), (float("inf"), 0), ("-inf", "0"), ("1e999", "0"),
                 (90.0001, 0), (-91, 0), (0, 180.0001), (0, -181),
                 (True, 0), (0, False), ([29.3], -95.8), ({"lat": 1}, 0)]
        for lat, lon in cases:
            with self.subTest(lat=lat, lon=lon):
                with self.assertRaises(ValueError) as caught:
                    soil_info.parse_coords(lat, lon)
                self.assertTrue(str(caught.exception).strip())

    def test_get_soil_info_rejects_bad_coordinates_before_fetching(self):
        sources = offline(self)
        for lat, lon in ((None, None), ("abc", 0), (95, 0), (0, float("inf"))):
            with self.subTest(lat=lat, lon=lon):
                with self.assertRaises(ValueError):
                    soil_info.get_soil_info(lat, lon)
        self.assertEqual(sources.count(), 0)


class PointKeyTests(SoilTestCase):
    def test_points_round_to_four_decimals(self):
        cases = [((29.364, -95.865), KEY_FB1),
                 ((29.36404, -95.86496), KEY_FB1),
                 ((29.36406, -95.86494), "29.3641,-95.8649"),
                 ((-12.5, 130.25), "-12.5000,130.2500"),
                 ((90.0, -180.0), "90.0000,-180.0000")]
        for point, key in cases:
            with self.subTest(point=point):
                self.assertEqual(soil_info.point_key(*point), key)

    def test_negative_zero_is_normalised(self):
        for point in ((-0.0, -0.0), (0.0, -0.0), (-0.00001, -0.00004), (0.00004, -0.00002)):
            with self.subTest(point=point):
                self.assertEqual(soil_info.point_key(*point), "0.0000,0.0000")


# ---------------------------------------------------------------------------
# HTTP and the two fetchers
# ---------------------------------------------------------------------------
class HttpJsonTests(SoilTestCase):
    """_http_json is the only caller of urlopen; inspect what it sends."""

    GET_URL = ("https://casoilresource.lawr.ucdavis.edu/api/landPKS.php"
               "?q=spn&lat=29.3640&lon=-95.8650&r=1")

    def capture(self, body=b'{"spn": []}', error=None, response=None):
        seen = {}
        if response is None:
            response = FakeResponse(body)

        def urlopen(request, data=None, timeout=None, **kwargs):
            seen.update(request=request, data=data, timeout=timeout)
            if error is not None:
                raise error
            return response

        patcher = mock.patch("urllib.request.urlopen", side_effect=urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        return seen, response

    def test_get_sends_the_user_agent_and_asks_for_json(self):
        seen, _ = self.capture(b'{"spn": [], "name": "Caf\xc3\xa9"}')
        result = soil_info._http_json(self.GET_URL, timeout=2.5)
        self.assertEqual(result, {"spn": [], "name": "Café"})
        request = seen["request"]
        self.assertIsInstance(request, urllib.request.Request)
        self.assertEqual(request.full_url, self.GET_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("User-agent"), soil_info.USER_AGENT)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertIsNotNone(seen["timeout"])
        self.assertGreater(seen["timeout"], 0)
        self.assertLessEqual(seen["timeout"], 2.5)

    def test_a_default_timeout_is_always_set(self):
        seen, _ = self.capture()
        soil_info._http_json(self.GET_URL)
        self.assertIsNotNone(seen["timeout"])
        self.assertGreater(seen["timeout"], 0)
        self.assertLessEqual(seen["timeout"], soil_info.TIMEOUT_SECONDS)

    def test_post_sends_json_with_a_content_type(self):
        seen, _ = self.capture(b'{"Table": []}')
        body = json.dumps({"query": "SELECT 1", "format": "JSON+COLUMNNAME"}).encode("utf-8")
        soil_info._http_json(soil_info.SDA_URL, data=body, timeout=3.0)
        request = seen["request"]
        self.assertEqual(request.full_url, soil_info.SDA_URL)
        self.assertEqual(request.get_method(), "POST")
        sent = request.data if request.data is not None else seen["data"]
        self.assertEqual(sent, body)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("User-agent"), soil_info.USER_AGENT)
        self.assertEqual(request.get_header("Accept"), "application/json")

    def test_the_body_is_read_with_a_cap(self):
        _, response = self.capture()
        soil_info._http_json(self.GET_URL, timeout=1.0)
        self.assertTrue(response.reads, "the response was never read")
        for amount in response.reads:
            self.assertIsInstance(amount, int, "read() without a size limit")
            self.assertLessEqual(amount, soil_info.MAX_RESPONSE_BYTES + 1)

    def test_an_oversized_body_is_refused(self):
        big = json.dumps({"spn": ["x" * 40] * 20}).encode("utf-8")
        self.capture(big)
        with mock.patch.object(soil_info, "MAX_RESPONSE_BYTES", 64):
            with self.assertRaises(Exception):
                soil_info._http_json(self.GET_URL, timeout=1.0)

    def test_a_streamed_body_is_read_in_capped_pieces(self):
        body = json.dumps({"spn": ["x" * 40] * 20}).encode("utf-8")
        _, response = self.capture(response=StreamingResponse(body, piece=100))
        self.assertEqual(soil_info._http_json(self.GET_URL, timeout=1.0),
                         {"spn": ["x" * 40] * 20})
        self.assertGreater(len(response.reads), 1)
        for amount in response.reads:
            self.assertIsInstance(amount, int, "read1() without a size limit")
            self.assertLessEqual(amount, soil_info.MAX_RESPONSE_BYTES + 1)

        self.capture(response=StreamingResponse(body, piece=100))
        with mock.patch.object(soil_info, "MAX_RESPONSE_BYTES", 64):
            with self.assertRaises(ValueError):
                soil_info._http_json(self.GET_URL, timeout=1.0)

    def test_a_trickling_reply_is_given_up_when_the_time_is_spent(self):
        # urlopen's timeout is per socket read, and every one-byte read here is
        # quick, so only _http_json's own clock can stop this (it would take
        # about 200 s to finish).
        body = b'{"spn": [' + b" " * 10000 + b"]}"
        _, response = self.capture(response=StreamingResponse(body, piece=1, pause=0.02))
        start = time.monotonic()
        with self.assertRaises(socket.timeout):
            soil_info._http_json(self.GET_URL, timeout=0.3)
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertGreater(len(response.reads), 1)

    def test_errors_propagate_to_the_caller(self):
        # Python 3.9 reports a slow read as a bare socket.timeout.
        for error in (socket.timeout("timed out"), urllib.error.URLError("refused")):
            with self.subTest(error=error):
                self.capture(error=error)
                with self.assertRaises(type(error)):
                    soil_info._http_json(self.GET_URL, timeout=1.0)
        self.capture(b"<html>Service Unavailable</html>")
        with self.assertRaises(ValueError):
            soil_info._http_json(self.GET_URL, timeout=1.0)


class FetchTests(SoilTestCase):
    def test_constants(self):
        self.assertEqual(soil_info.USER_AGENT, "FarmersForward/1.0 (soil-profile lookup; "
                         "+https://github.com/atatti-240/farmers-forward)")
        self.assertEqual(soil_info.TIMEOUT_SECONDS, 10.0)
        self.assertEqual(soil_info.CACHE_MAX_AGE_DAYS, 365)
        self.assertEqual(soil_info.SDA_URL,
                         "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest")
        self.assertTrue(soil_info.SOILWEB_URL.startswith(
            "https://casoilresource.lawr.ucdavis.edu/api/landPKS.php"))
        self.assertGreater(soil_info.MAX_RESPONSE_BYTES, 100000)

    def test_sda_sql_is_the_query_the_fixtures_were_recorded_with(self):
        with open(os.path.join(FIXTURES, "sda_query.sql"), encoding="utf-8") as fh:
            recorded = fh.read()
        self.assertEqual(" ".join(soil_info.SDA_SQL.split()), " ".join(recorded.split()))
        fields = [name for _, name, _, _ in string.Formatter().parse(soil_info.SDA_SQL)
                  if name is not None]
        self.assertEqual(sorted(fields), ["lat", "lon"])

    def test_fetch_sda_posts_the_query_for_the_point_and_returns_the_row(self):
        http = FakeHttp(self, soilweb=AssertionError("not SoilWeb"),
                        sda=load("sda_fb1.json"))
        row = soil_info._fetch_sda(FB1[0], FB1[1], 3.0)
        self.assertEqual(row["muname"], "Lake Charles clay, 0 to 1 percent slopes")
        self.assertEqual(row["om_r"], "4")
        self.assertEqual(row["cec7_r"], "48.6")
        self.assertEqual(row["data_status"], "dominant")
        self.assertEqual(row["hydgrp"], "D")

        (request,) = http.requests["sda"]
        self.assertEqual(request["url"], soil_info.SDA_URL)
        self.assertIsInstance(request["data"], bytes)
        self.assertEqual(json.loads(request["data"].decode("utf-8")), {
            "query": soil_info.SDA_SQL.format(lon="-95.8650", lat="29.3640"),
            "format": "JSON+COLUMNNAME"})
        self.assertGreater(request["timeout"], 0)
        self.assertLessEqual(request["timeout"], 3.0)

    def test_fetch_sda_raises_when_sda_has_no_row(self):
        header, values = load("sda_fb1.json")["Table"]
        bodies = {
            "empty object (SDA's reply to no rows or an error)": {},
            "no Table key": {"Error": "something"},
            "empty Table": {"Table": []},
            "header only": {"Table": [header]},
            "row shorter than header": {"Table": [header, values[:-1]]},
            "not an object": [],
        }
        for label, body in bodies.items():
            with self.subTest(body=label):
                FakeHttp(self, soilweb=AssertionError("not SoilWeb"), sda=body)
                with self.assertRaises(Exception):
                    soil_info._fetch_sda(FB1[0], FB1[1], 3.0)

    def test_fetch_soilweb_asks_for_the_map_unit_under_the_point(self):
        http = FakeHttp(self, soilweb=load("soilweb_fb1.json"),
                        sda=AssertionError("not SDA"))
        raw = soil_info._fetch_soilweb(FB1[0], FB1[1], 2.0)
        self.assertEqual(raw, load("soilweb_fb1.json"))

        (request,) = http.requests["soilweb"]
        parts = urllib.parse.urlsplit(request["url"])
        self.assertEqual((parts.scheme, parts.netloc, parts.path),
                         ("https", "casoilresource.lawr.ucdavis.edu", "/api/landPKS.php"))
        query = urllib.parse.parse_qs(parts.query)
        self.assertEqual(query["q"], ["spn"])
        self.assertEqual(query["r"], ["1"])
        self.assertAlmostEqual(float(query["lat"][0]), 29.364, places=9)
        self.assertAlmostEqual(float(query["lon"][0]), -95.865, places=9)
        self.assertIsNone(request["data"])
        self.assertGreater(request["timeout"], 0)
        self.assertLessEqual(request["timeout"], 2.0)


class SoilWebParseTests(SoilTestCase):
    """_parse_soilweb on the recorded landPKS bodies and edge cases."""

    def test_fb1_dominant_component_and_surface_horizon(self):
        parsed = soil_info._parse_soilweb(load("soilweb_fb1.json"))
        self.assertTrue(parsed["has_data"])
        self.assertEqual(parsed["series"], "Lake Charles")
        self.assertEqual(str(parsed["cokey"]), "28117893")
        self.assertEqual(parsed["component_pct"], 90)
        self.assertEqual(parsed["component_kind"], "Series")
        self.assertEqual(parsed["texture"], "Clay")
        self.assertAlmostEqual(parsed["sand_pct"], 11.4)
        self.assertAlmostEqual(parsed["silt_pct"], 37.4)
        self.assertAlmostEqual(parsed["clay_pct"], 51.2)
        self.assertAlmostEqual(parsed["cec"], 48.6)
        self.assertEqual((parsed["layer_top_cm"], parsed["layer_bottom_cm"]), (0, 29))

    def test_fb2_bernard(self):
        parsed = soil_info._parse_soilweb(load("soilweb_fb2.json"))
        self.assertTrue(parsed["has_data"])
        self.assertEqual(parsed["series"], "Bernard")
        self.assertEqual(parsed["component_pct"], 60)
        self.assertEqual(parsed["texture"].lower(), "clay loam")
        self.assertTrue(parsed["texture"][0].isupper(), parsed["texture"])
        self.assertAlmostEqual(parsed["sand_pct"], 26.1)
        self.assertAlmostEqual(parsed["silt_pct"], 43.4)
        self.assertAlmostEqual(parsed["clay_pct"], 30.5)
        self.assertAlmostEqual(parsed["cec"], 28.6)
        self.assertEqual((parsed["layer_top_cm"], parsed["layer_bottom_cm"]), (0, 15))

    def test_the_empty_body_has_no_data(self):
        parsed = soil_info._parse_soilweb(load("soilweb_empty.json"))
        self.assertFalse(parsed["has_data"])
        self.assertIsNone(parsed["series"])

    def test_neighbouring_map_units_are_ignored(self):
        raw = load("soilweb_fb1.json")
        raw["spn"].append(dict(raw["spn"][1], which="b", mukey=999999, cokey=99999999,
                               compname="cieno", comppct_r=99, majcompflag="Yes",
                               dist_meters=40))
        self.assertEqual(soil_info._parse_soilweb(raw)["series"], "Lake Charles")
        for row in raw["spn"]:
            row["which"] = "b"
        self.assertFalse(soil_info._parse_soilweb(raw)["has_data"])

    def test_ties_go_to_the_major_component_then_the_lowest_cokey(self):
        def spn(cokey, name, pct, major):
            return {"mukey": 1, "dist_meters": 0, "which": "a", "cokey": cokey,
                    "compname": name, "comppct_r": pct, "compkind": "Series",
                    "majcompflag": major}

        def body(*rows):
            return {"spn": list(rows), "ESD": [], "OSD_narrative": [],
                    "OSD_morph": [], "hz": []}

        # SoilWeb pads majcompflag: 'No ' with a trailing space.
        major_wins = body(spn(28117895, "alpha", 45, "No "), spn(28117893, "beta", 45, "Yes"))
        self.assertEqual(soil_info._parse_soilweb(major_wins)["series"], "Beta")
        lowest_cokey = body(spn(28117895, "epsilon", 45, "Yes"), spn(28117893, "delta", 45, "Yes"))
        self.assertEqual(soil_info._parse_soilweb(lowest_cokey)["series"], "Delta")

    def test_horizon_order_and_placeholder_rows_do_not_matter(self):
        raw = load("soilweb_fb1.json")
        raw["hz"].reverse()
        placeholder = dict.fromkeys(raw["hz"][0])
        placeholder.update(mukey=365945, cokey=28117893)
        raw["hz"].append(placeholder)
        parsed = soil_info._parse_soilweb(raw)
        self.assertEqual((parsed["layer_top_cm"], parsed["layer_bottom_cm"]), (0, 29))
        self.assertAlmostEqual(parsed["clay_pct"], 51.2)


# ---------------------------------------------------------------------------
# Status rules, on the recorded responses
# ---------------------------------------------------------------------------
COMPONENT_COLUMNS = ("dom_compname", "dom_comppct_r", "dom_compkind", "cokey",
                     "compname", "comppct_r", "compkind", "majcompflag", "drainagecl",
                     "hydgrp", "taxclname", "chkey", "hzname", "hzdept_r", "hzdepb_r",
                     "om_l", "om_r", "om_h", "sandtotal_r", "silttotal_r",
                     "claytotal_r", "cec7_r", "texture", "texdesc")


class StatusRuleTests(SoilTestCase):
    """Contract A's rules 1-7. Only _http_json is replaced here."""

    def lookup(self, point, soilweb, sda):
        self.http = FakeHttp(self, soilweb, sda)
        payload = soil_info.get_soil_info(*point)
        assert_payload_shape(self, payload)
        return payload

    def assert_cached(self, point, expected=True):
        key = soil_info.point_key(*point)
        if expected:
            self.assertIsNotNone(cache_row(key), "%s should have been cached" % key)
        else:
            self.assertIsNone(cache_row(key), "%s must not be cached" % key)

    def assert_no_properties(self, payload, keep=()):
        for key in PROPERTY_KEYS:
            if key not in keep:
                self.assertIsNone(payload[key], key)

    # --- rule 1: SDA has the soil -------------------------------------------
    def test_fb1_lake_charles_is_ok_from_both_sources(self):
        payload = self.lookup(FB1, load("soilweb_fb1.json"), load("sda_fb1.json"))
        self.assertEqual(comparable(payload), comparable(PROFILE_FB1))
        for phrase in ("SSURGO", SOILWEB, SDA, "not a measurement of this field"):
            self.assertIn(phrase, payload["source_note"])
        self.assertFalse(payload["cached"])
        self.assert_cached(FB1)

    def test_the_lookup_is_made_at_the_rounded_point(self):
        payload = self.lookup((29.36404, -95.86496), load("soilweb_fb1.json"),
                              load("sda_fb1.json"))
        self.assertEqual((payload["lat"], payload["lon"], payload["point_key"]),
                         (29.364, -95.865, KEY_FB1))
        (sda,) = self.http.requests["sda"]
        self.assertEqual(json.loads(sda["data"].decode("utf-8"))["query"],
                         soil_info.SDA_SQL.format(lon="-95.8650", lat="29.3640"))
        (soilweb,) = self.http.requests["soilweb"]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(soilweb["url"]).query)
        self.assertEqual((float(query["lat"][0]), float(query["lon"][0])), (29.364, -95.865))
        for request in (sda, soilweb):
            self.assertGreater(request["timeout"], 0)
            self.assertLessEqual(request["timeout"], soil_info.TIMEOUT_SECONDS)

    def test_fb2_bernard_is_ok(self):
        payload = self.lookup(FB2, load("soilweb_fb2.json"), load("sda_fb2.json"))
        expected = dict(
            status="ok", reason_code=None, reason=None, series="Bernard",
            component_pct=60, component_kind="Series",
            map_unit="Bernard-Edna complex, 0 to 1 percent slopes", map_unit_symbol="Be",
            survey_area="Fort Bend County, Texas", texture="Clay loam", sand_pct=26.1,
            silt_pct=43.4, clay_pct=30.5, cec=28.6, drainage="Somewhat poorly drained",
            hydrologic_group="D", organic_matter_pct=2.7, organic_matter_low=1.3,
            organic_matter_high=3.4, layer_top_cm=0, layer_bottom_cm=15,
            sources=[SOILWEB, SDA])
        self.assertEqual({key: payload[key] for key in expected}, expected)

    def test_a_fallback_component_is_ok_and_named_by_sda(self):
        # The dominant component had no organic matter, so SDA picked Edna.
        row = sda_row("sda_fb2.json", cokey="28117874", compname="Edna",
                      comppct_r="20", data_status="fallback", chkey="84411390",
                      hzname="A", hzdept_r="0", hzdepb_r="23", om_l="0.5", om_r="1",
                      om_h="2", sandtotal_r="37.9", silttotal_r="41.5",
                      claytotal_r="20.6", cec7_r=None, texture="L", texdesc="Loam")
        payload = self.lookup(FB2, load("soilweb_fb2.json"), sda_body(row))
        self.assertEqual(payload["status"], "ok")
        self.assertIsNone(payload["reason_code"])
        self.assertEqual(payload["series"], "Edna")
        self.assertEqual(payload["component_pct"], 20)
        self.assertEqual(payload["texture"], "Loam")
        self.assertEqual(payload["organic_matter_pct"], 1.0)
        self.assertIsNone(payload["cec"])
        self.assertEqual((payload["layer_top_cm"], payload["layer_bottom_cm"]), (0, 23))
        self.assert_cached(FB2)

    def test_when_the_sources_disagree_sda_names_the_series_and_it_is_logged(self):
        with self.assertLogs("backend.soil_info", level="WARNING"):
            payload = self.lookup(FB1, load("soilweb_fb2.json"), load("sda_fb1.json"))
        # Every property comes from SDA's component, so the profile is one soil.
        # Whether SoilWeb still counts as a source when its series lost is
        # left to soil_info.
        expected = comparable(PROFILE_FB1)
        actual = comparable(payload)
        self.assertIn(SDA, actual.pop("sources"))
        expected.pop("sources")
        self.assertEqual(actual, expected)

    def test_sda_texture_falls_back_to_the_soilweb_series_description(self):
        row = sda_row("sda_fb1.json", texture=None, texdesc=None)
        payload = self.lookup(FB1, load("soilweb_fb1.json"), sda_body(row))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["texture"], "Clay")
        # The note credits the texture to SoilWeb, not to Soil Data Access.
        self.assertIn("soil series and texture via UC Davis SoilWeb", payload["source_note"])
        self.assertIn("other soil properties via USDA Soil Data Access",
                      payload["source_note"])
        clear_cache()
        usual = self.lookup(FB1, load("soilweb_fb1.json"), load("sda_fb1.json"))
        self.assertEqual(usual["source_note"], SOURCE_NOTE)

    def test_blank_sda_values_are_missing_not_zero(self):
        row = sda_row("sda_fb1.json", cec7_r="", om_h="", sandtotal_r="")
        payload = self.lookup(FB1, load("soilweb_fb1.json"), sda_body(row))
        self.assertEqual(payload["status"], "ok")
        self.assertIsNone(payload["cec"])
        self.assertIsNone(payload["organic_matter_high"])
        self.assertIsNone(payload["sand_pct"])
        self.assertEqual(payload["organic_matter_pct"], 4.0)

    def test_organic_matter_rounds_half_up_from_the_survey_string(self):
        # Float rounding gives 1.1 for 1.15 and banker's rounding 0.2 for 0.25.
        row = sda_row("sda_fb1.json", om_l="0.25", om_r="1.15", om_h="2.25")
        payload = self.lookup(FB1, load("soilweb_fb1.json"), sda_body(row))
        self.assertEqual((payload["organic_matter_low"], payload["organic_matter_pct"],
                          payload["organic_matter_high"]), (0.3, 1.2, 2.3))

    # --- rule 2: SDA has a map unit but no surface organic matter ------------
    def test_a_water_map_unit_has_no_soil_data(self):
        payload = self.lookup(WATER2, load("soilweb_empty.json"),
                              load("sda_water2_water.json"))
        self.assertEqual(payload["status"], "no_data")
        self.assertEqual(payload["reason_code"], "non_soil_map_unit")
        self.assertIn('"Water"', payload["reason"])
        self.assertEqual(payload["map_unit"], "Water")
        self.assertEqual(payload["survey_area"], "Fort Bend County, Texas")
        self.assertEqual(payload["hydrologic_group"], "D")
        self.assert_no_properties(payload, keep=("series", "component_pct",
                                                 "hydrologic_group"))
        self.assert_cached(WATER2)

    def test_urban_land_has_no_soil_data(self):
        # Urban land does have a horizon row; its component kind gives it away.
        payload = self.lookup(URBAN1, load("soilweb_empty.json"), load("sda_urban1.json"))
        self.assertEqual(payload["status"], "no_data")
        self.assertEqual(payload["reason_code"], "non_soil_map_unit")
        self.assertIn("Urban land", payload["reason"])
        self.assertEqual(payload["map_unit"], "Urban land")
        self.assertEqual(payload["survey_area"], "Harris County, Texas")
        self.assertIsNone(payload["organic_matter_pct"])
        self.assert_cached(URBAN1)

    def test_a_real_soil_without_recorded_organic_matter_is_still_ok(self):
        row = sda_row("sda_fb1.json", om_l=None, om_r=None, om_h=None,
                      data_status="no_surface_om")
        payload = self.lookup(FB1, load("soilweb_fb1.json"), sda_body(row))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["reason_code"], "om_not_in_survey")
        self.assertEqual(payload["reason"], REASON_OM_NOT_RECORDED)
        self.assertIsNone(payload["organic_matter_pct"])
        self.assertIsNone(payload["organic_matter_low"])
        self.assertIsNone(payload["organic_matter_high"])
        self.assertEqual(payload["series"], "Lake Charles")
        self.assertEqual(payload["texture"], "Clay")
        self.assertEqual(payload["drainage"], "Moderately well drained")
        self.assert_cached(FB1)

    # --- rules 3 and 4: SDA says there is nothing here -----------------------
    def test_a_map_unit_without_components_has_no_data(self):
        row = sda_row("sda_water2_water.json", data_status="no_components",
                      **dict.fromkeys(COMPONENT_COLUMNS))
        payload = self.lookup(WATER2, load("soilweb_empty.json"), sda_body(row))
        self.assertEqual(payload["status"], "no_data")
        self.assertEqual(payload["reason_code"], "no_components")
        self.assert_no_properties(payload)
        self.assert_cached(WATER2)

    def test_open_water_in_the_gulf_has_no_survey_coverage(self):
        payload = self.lookup(WATER1, load("soilweb_empty.json"),
                              load("sda_water1_no_mukey.json"))
        self.assertEqual(payload["status"], "no_data")
        self.assertEqual(payload["reason_code"], "no_survey_coverage")
        self.assertEqual(payload["reason"], REASON_NO_MAP_UNIT)
        self.assertIsNone(payload["map_unit"])
        self.assert_no_properties(payload)
        self.assert_cached(WATER1)
        self.assertTrue(soil_info.get_soil_info(*WATER1)["cached"])

    # --- rules 5-7: SDA did not answer ---------------------------------------
    def test_sda_failing_leaves_a_partial_profile_that_is_not_cached(self):
        header = load("sda_fb1.json")["Table"][0]
        failures = [("timeout", socket.timeout("timed out")),
                    ("empty object", {}),
                    ("header only", {"Table": [header]}),
                    ("refused", urllib.error.URLError("connection refused"))]
        for label, sda in failures:
            with self.subTest(sda=label):
                clear_cache()
                payload = self.lookup(FB1, load("soilweb_fb1.json"), sda)
                self.assertEqual(payload["status"], "partial")
                self.assertEqual(payload["reason_code"], "properties_unavailable")
                self.assertEqual(payload["reason"], REASON_PARTIAL)
                expected = dict(series="Lake Charles", component_pct=90, texture="Clay",
                                sand_pct=11.4, silt_pct=37.4, clay_pct=51.2, cec=48.6,
                                layer_top_cm=0, layer_bottom_cm=29, organic_matter_pct=None,
                                organic_matter_low=None, organic_matter_high=None,
                                drainage=None, hydrologic_group=None)
                self.assertEqual({key: payload[key] for key in expected}, expected)
                self.assertEqual(payload["sources"], [SOILWEB])
                self.assertNotIn("Soil Data Access", payload["source_note"])
                self.assert_cached(FB1, False)

        # Nothing was kept, so the next lookup asks again.
        again = self.lookup(FB1, load("soilweb_fb1.json"), socket.timeout("timed out"))
        self.assertFalse(again["cached"])
        self.assertEqual(len(self.http.requests["sda"]), 1)

    def test_sda_failing_with_no_soilweb_component_is_no_data_and_not_cached(self):
        neighbours_only = load("soilweb_fb1.json")
        for row in neighbours_only["spn"]:
            row["which"] = "b"
        for label, soilweb in (("empty", load("soilweb_empty.json")),
                               ("neighbours only", neighbours_only)):
            with self.subTest(soilweb=label):
                payload = self.lookup(WATER1, soilweb, socket.timeout("timed out"))
                self.assertEqual(payload["status"], "no_data")
                self.assertEqual(payload["reason_code"], "no_survey_coverage")
                self.assertEqual(payload["reason"], REASON_NO_DATA)
                self.assert_no_properties(payload)
                self.assert_cached(WATER1, False)

    def test_both_services_failing_is_unavailable_and_not_cached(self):
        payload = self.lookup(FB1, socket.timeout("timed out"),
                              urllib.error.URLError("refused"))
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason_code"], "unavailable")
        self.assertEqual(payload["reason"], REASON_UNAVAILABLE)
        self.assertEqual(payload["sources"], [])
        self.assert_no_properties(payload)
        self.assertIsNone(payload["map_unit"])
        self.assert_cached(FB1, False)

    def test_soilweb_failing_alone_still_gives_a_cached_ok_profile(self):
        payload = self.lookup(FB1, socket.timeout("timed out"), load("sda_fb1.json"))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["series"], "Lake Charles")
        self.assertEqual(payload["organic_matter_pct"], 4.0)
        self.assertEqual(payload["texture"], "Clay")
        self.assertEqual(payload["sources"], [SDA])
        self.assertNotIn("SoilWeb", payload["source_note"])
        self.assert_cached(FB1)

    # --- whatever happened ----------------------------------------------------
    def test_every_outcome_has_the_same_keys(self):
        timeout = socket.timeout("timed out")
        scenarios = [
            (FB1, load("soilweb_fb1.json"), load("sda_fb1.json")),
            (FB1, timeout, load("sda_fb1.json")),
            (FB1, load("soilweb_fb1.json"), timeout),
            (WATER1, load("soilweb_empty.json"), load("sda_water1_no_mukey.json")),
            (WATER2, load("soilweb_empty.json"), load("sda_water2_water.json")),
            (URBAN1, load("soilweb_empty.json"), load("sda_urban1.json")),
            (WATER1, load("soilweb_empty.json"), timeout),
            (FB1, timeout, timeout),
        ]
        key_sets = []
        for point, soilweb, sda in scenarios:
            clear_cache()
            key_sets.append(frozenset(self.lookup(point, soilweb, sda)))
        self.assertEqual(len(set(key_sets)), 1, "payload keys differ between outcomes")

    def test_malformed_soilweb_bodies_never_raise(self):
        bodies = [{"spn": "oops"}, [], "text", {"spn": [{"which": "a"}]},
                  {"spn": [{"which": "a", "compname": None, "comppct_r": None}], "hz": None}]
        for body in bodies:
            with self.subTest(body=body):
                clear_cache()
                self.lookup(FB1, body, load("sda_fb1.json"))

    def test_combine_reports_whether_the_answer_may_be_cached(self):
        fb1 = soil_info._parse_soilweb(load("soilweb_fb1.json"))
        payload, cacheable = soil_info._combine(fb1, sda_row("sda_fb1.json"), None, None)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(cacheable)
        payload, cacheable = soil_info._combine(
            None, sda_row("sda_water1_no_mukey.json"), "timeout", None)
        self.assertEqual(payload["status"], "no_data")
        self.assertTrue(cacheable)
        payload, cacheable = soil_info._combine(fb1, None, None, "timeout")
        self.assertEqual(payload["status"], "partial")
        self.assertFalse(cacheable)
        payload, cacheable = soil_info._combine(None, None, "timeout", "timeout")
        self.assertEqual(payload["status"], "unavailable")
        self.assertFalse(cacheable)


# ---------------------------------------------------------------------------
# The deadline
# ---------------------------------------------------------------------------
class DeadlineTests(SoilTestCase):
    """One wall-clock budget for both services, enforced by not waiting."""

    # A HANG source holds out for 5 s, so finishing well inside that proves
    # the lookup stopped waiting at its 0.5 s budget; the slack is for a busy
    # test machine.
    HUNG_BOUND = 3.0

    def timed_lookup(self, point=FB1):
        start = time.monotonic()
        payload = soil_info.get_soil_info(*point)
        return payload, time.monotonic() - start

    def test_a_hung_sda_cannot_hold_the_lookup_past_the_deadline(self):
        with mock.patch.object(soil_info, "TIMEOUT_SECONDS", 0.5):
            FakeSources(self, soilweb=load("soilweb_fb1.json"), sda=HANG)
            payload, elapsed = self.timed_lookup()
        self.assertLess(elapsed, self.HUNG_BOUND)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["series"], "Lake Charles")
        self.assertIsNone(cache_row(KEY_FB1))

    def test_both_services_hanging_is_unavailable_within_the_deadline(self):
        with mock.patch.object(soil_info, "TIMEOUT_SECONDS", 0.5):
            FakeSources(self, soilweb=HANG, sda=HANG)
            payload, elapsed = self.timed_lookup()
        self.assertLess(elapsed, self.HUNG_BOUND)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIsNone(cache_row(KEY_FB1))

    def test_a_hung_soilweb_does_not_cost_the_sda_answer(self):
        with mock.patch.object(soil_info, "TIMEOUT_SECONDS", 0.5):
            FakeSources(self, soilweb=HANG, sda=sda_row("sda_fb1.json"))
            payload, elapsed = self.timed_lookup()
        self.assertLess(elapsed, self.HUNG_BOUND)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["sources"], [SDA])
        self.assertIsNotNone(cache_row(KEY_FB1))

    def test_the_services_are_asked_at_the_same_time(self):
        sources = fb1_sources(self, delay=0.4)
        payload, _ = self.timed_lookup()
        self.assertEqual(payload["status"], "ok")
        # Structural rather than a stopwatch: both were under way before
        # either had answered.
        self.assertEqual(set(sources.started), {"soilweb", "sda"})
        self.assertLess(max(sources.started.values()), min(sources.finished.values()),
                        "the two lookups ran one after the other")
        for source in ("soilweb", "sda"):
            (lat, lon, timeout) = sources.calls[source][0]
            self.assertIsNotNone(timeout, "%s got no timeout" % source)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, soil_info.TIMEOUT_SECONDS)

    def test_the_fetchers_get_the_rounded_point(self):
        sources = fb1_sources(self)
        soil_info.get_soil_info(29.36404, -95.86496)
        for source in ("soilweb", "sda"):
            (lat, lon, _) = sources.calls[source][0]
            self.assertAlmostEqual(lat, 29.364, places=9)
            self.assertAlmostEqual(lon, -95.865, places=9)


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------
class CacheTests(SoilTestCase):
    def age_row(self, days, key=KEY_FB1):
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        database.execute("UPDATE soil_info_cache SET fetched_at = ? WHERE point_key = ?",
                         (stamp, key))
        return stamp

    def test_a_second_lookup_is_served_from_the_cache(self):
        sources = fb1_sources(self)
        first = soil_info.get_soil_info(*FB1)
        second = soil_info.get_soil_info(*FB1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(sources.calls["soilweb"]), 1)
        self.assertEqual(len(sources.calls["sda"]), 1)
        first.pop("cached")
        second.pop("cached")
        self.assertEqual(first, second)

    def test_a_nearby_point_that_rounds_to_the_same_key_is_a_hit(self):
        fb1_sources(self)
        soil_info.get_soil_info(*FB1)
        sources = offline(self)
        payload = soil_info.get_soil_info(29.36404, -95.86496)
        self.assertTrue(payload["cached"])
        self.assertEqual(payload["point_key"], KEY_FB1)
        self.assertEqual(sources.count(), 0)

    def test_the_stored_row_has_no_cached_flag(self):
        fb1_sources(self)
        soil_info.get_soil_info(*FB1)
        row = cache_row(KEY_FB1)
        stored = json.loads(row["payload"])
        self.assertNotIn("cached", stored)
        self.assertEqual(stored["status"], "ok")
        self.assertEqual(parse_utc(row["fetched_at"]).utcoffset(), timedelta(0))

    def test_the_table_is_created_by_soil_info_with_the_contract_columns(self):
        fb1_sources(self)
        soil_info.get_soil_info(*FB1)
        columns = {row["name"]: row for row in
                   database.query_all("PRAGMA table_info(soil_info_cache)")}
        self.assertEqual(set(columns), {"id", "point_key", "payload", "fetched_at"})
        self.assertEqual(columns["id"]["pk"], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            database.execute("INSERT INTO soil_info_cache (point_key, payload, fetched_at)"
                             " VALUES (?, ?, ?)", (KEY_FB1, "{}", database.now_iso()))

    def test_cached_soil_info_reads_the_cache_and_never_the_network(self):
        sources = offline(self)
        self.assertIsNone(soil_info.cached_soil_info(*FB1))
        self.assertEqual(sources.count(), 0)

        fb1_sources(self)
        soil_info.get_soil_info(*FB1)
        sources = offline(self)
        for point in (FB1, ("29.364", "-95.865"), (29.36404, -95.86496)):
            with self.subTest(point=point):
                hit = soil_info.cached_soil_info(*point)
                self.assertIsNotNone(hit)
                self.assertEqual(hit["status"], "ok")
                self.assertEqual(hit["series"], "Lake Charles")
        self.assertIsNone(soil_info.cached_soil_info(*FB2))
        self.assertEqual(sources.count(), 0)

    def test_cached_soil_info_returns_none_for_bad_coordinates(self):
        sources = offline(self)
        for point in ((None, None), ("abc", 0), (95, 0), (float("nan"), 0), (True, 0)):
            with self.subTest(point=point):
                self.assertIsNone(soil_info.cached_soil_info(*point))
        self.assertEqual(sources.count(), 0)

    def test_a_stale_row_is_refetched_and_replaced(self):
        sources = fb1_sources(self)
        soil_info.get_soil_info(*FB1)
        stale = self.age_row(soil_info.CACHE_MAX_AGE_DAYS + 1)
        self.assertIsNone(soil_info.cached_soil_info(*FB1))

        again = soil_info.get_soil_info(*FB1)
        self.assertFalse(again["cached"])
        self.assertEqual(len(sources.calls["sda"]), 2)
        row = cache_row(KEY_FB1)
        self.assertGreater(parse_utc(row["fetched_at"]), parse_utc(stale))
        self.assertTrue(soil_info.get_soil_info(*FB1)["cached"])

    def test_a_row_inside_the_window_is_still_fresh(self):
        sources = fb1_sources(self)
        soil_info.get_soil_info(*FB1)
        self.age_row(soil_info.CACHE_MAX_AGE_DAYS - 1)
        self.assertTrue(soil_info.get_soil_info(*FB1)["cached"])
        self.assertIsNotNone(soil_info.cached_soil_info(*FB1))
        self.assertEqual(len(sources.calls["sda"]), 1)

    def test_a_corrupt_row_is_a_miss_not_an_error(self):
        fb1_sources(self)
        soil_info.get_soil_info(*FB1)
        database.execute("UPDATE soil_info_cache SET payload = ? WHERE point_key = ?",
                         ("{not json", KEY_FB1))
        self.assertIsNone(soil_info.cached_soil_info(*FB1))
        payload = soil_info.get_soil_info(*FB1)
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["cached"])

    def test_partial_and_unavailable_answers_are_never_cached(self):
        timeout = socket.timeout("timed out")
        for soilweb, status in ((load("soilweb_fb1.json"), "partial"),
                                (timeout, "unavailable")):
            with self.subTest(status=status):
                sources = FakeSources(self, soilweb=soilweb, sda=timeout)
                self.assertEqual(soil_info.get_soil_info(*FB1)["status"], status)
                self.assertIsNone(cache_row(KEY_FB1))
                self.assertIsNone(soil_info.cached_soil_info(*FB1))
                self.assertFalse(soil_info.get_soil_info(*FB1)["cached"])
                self.assertEqual(len(sources.calls["sda"]), 2)

    def test_database_errors_never_escape(self):
        broken = mock.MagicMock()
        for attr in ("execute", "executescript", "commit", "cursor"):
            getattr(broken, attr).side_effect = sqlite3.DatabaseError("disk I/O error")
        failures = {
            "cannot connect": dict(side_effect=sqlite3.OperationalError("database is locked")),
            "query fails": dict(return_value=broken),
        }
        fb1_sources(self)
        for label, behaviour in failures.items():
            with self.subTest(database=label):
                with mock.patch.object(database, "get_db", **behaviour):
                    payload = soil_info.get_soil_info(*FB1)
                    self.assertIsNone(soil_info.cached_soil_info(*FB1))
                self.assertEqual(payload["status"], "ok")
                self.assertFalse(payload["cached"])

        # Once the database is back, the lookup caches as normal.
        self.assertFalse(soil_info.get_soil_info(*FB1)["cached"])
        self.assertTrue(soil_info.get_soil_info(*FB1)["cached"])

    def test_a_locked_database_does_not_hold_up_the_lookup(self):
        # /api/submit holds SQLite's write lock from its farms INSERT until it
        # commits. A lookup meanwhile must not sit out the 5 s busy timeout,
        # and its answer should still be cached once the lock is released.
        fb1_sources(self)
        self.assertIsNone(soil_info.cached_soil_info(*FB1))  # the table exists now
        other = sqlite3.connect(config.DATABASE_PATH)
        self.addCleanup(other.close)
        other.execute("INSERT INTO soil_info_cache (point_key, payload, fetched_at) "
                      "VALUES ('held', '{}', '2026-10-23T00:00:00+00:00')")
        try:
            start = time.monotonic()
            payload = soil_info.get_soil_info(*FB1)
            elapsed = time.monotonic() - start
        finally:
            other.rollback()
        self.assertEqual(payload["status"], "ok")
        self.assertLess(elapsed, 2.5, "the lookup waited on the database lock")
        for thread in threading.enumerate():
            if thread.name == "soil-info-cache":
                thread.join(6)
        self.assertIsNotNone(cache_row(KEY_FB1), "the answer was not cached afterwards")

    def test_importing_soil_info_touches_neither_network_nor_database(self):
        touched = []

        def refuse(*args, **kwargs):
            touched.append(args[:1])
            raise AssertionError("touched at import time")

        spec = importlib.util.spec_from_file_location("backend._soil_info_import_probe",
                                                      soil_info.__file__)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(database, "get_db", side_effect=refuse), \
                mock.patch("sqlite3.connect", side_effect=refuse), \
                mock.patch("urllib.request.urlopen", side_effect=refuse), \
                mock.patch("socket.create_connection", side_effect=refuse):
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            finally:
                sys.modules.pop(spec.name, None)
        self.assertEqual(touched, [])


# ---------------------------------------------------------------------------
# GET /api/soil-info
# ---------------------------------------------------------------------------
class SoilInfoRouteTests(SoilTestCase):
    def setUp(self):
        super().setUp()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True
            session["admin_id"] = 1

    def test_requires_login(self):
        sources = offline(self)
        response = app.test_client().get("/api/soil-info?lat=29.364&lon=-95.865")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(sources.count(), 0)

    def test_bad_or_missing_coordinates_are_400(self):
        sources = offline(self)
        for query in ("", "?lat=29.364", "?lon=-95.865", "?lat=&lon=", "?lat=abc&lon=-95.865",
                      "?lat=nan&lon=0", "?lat=inf&lon=0", "?lat=91&lon=0", "?lat=0&lon=181"):
            with self.subTest(query=query):
                response = self.client.get("/api/soil-info" + query)
                self.assertEqual(response.status_code, 400)
                self.assertIsInstance(response.json.get("error"), str)
                self.assertTrue(response.json["error"].strip())
        self.assertEqual(sources.count(), 0)

    def test_returns_the_payload(self):
        fb1_sources(self)
        response = self.client.get("/api/soil-info?lat=29.36404&lon=-95.86496")
        self.assertEqual(response.status_code, 200)
        assert_payload_shape(self, response.json)
        self.assertEqual(comparable(response.json), comparable(PROFILE_FB1))
        self.assertFalse(response.json["cached"])
        self.assertTrue(self.client.get("/api/soil-info?lat=29.364&lon=-95.865")
                        .json["cached"])

    def test_services_being_down_is_still_200(self):
        timeout = socket.timeout("timed out")
        FakeSources(self, soilweb=timeout, sda=timeout)
        response = self.client.get("/api/soil-info?lat=29.364&lon=-95.865")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "unavailable")


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


class SubmitSoilPointTests(SoilTestCase):
    def setUp(self):
        super().setUp()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True
            session["admin_id"] = 1
        patcher = mock.patch.object(model_runner, "_predict", side_effect=predictions)
        patcher.start()
        self.addCleanup(patcher.stop)

    def warm_cache(self):
        fb1_sources(self)
        soil_info.get_soil_info(*FB1)

    def submit(self, **extra):
        body = {"farm_name": "Soil point field", "date": "2026-10-23",
                "sections": [dict(SECTION)]}
        body.update(extra)
        response = self.client.post("/api/submit", json=body)
        if response.status_code == 200:
            self.addCleanup(forget_farm, response.json["farm_id"])
        return response

    def stored(self, response):
        return self.client.get("/api/results/%d" % response.json["farm_id"]).json

    def test_a_cached_profile_is_stored_with_the_submission(self):
        self.warm_cache()
        sources = offline(self)
        response = self.submit(soil_point={"lat": 29.36401, "lon": -95.86502})
        self.assertEqual(response.status_code, 200, response.json)
        for analysis in (response.json, self.stored(response)):
            self.assertEqual(analysis["soil_point"], {"lat": 29.364, "lon": -95.865})
            self.assertEqual(analysis["soil_profile"]["status"], "ok")
            self.assertEqual(analysis["soil_profile"]["series"], "Lake Charles")
            self.assertEqual(analysis["soil_profile"]["point_key"], KEY_FB1)
        self.assertEqual(sources.count(), 0)

    def test_an_uncached_point_stores_a_null_profile(self):
        sources = offline(self)
        response = self.submit(soil_point={"lat": "29.364", "lon": "-95.865"})
        self.assertEqual(response.status_code, 200, response.json)
        for analysis in (response.json, self.stored(response)):
            self.assertEqual(analysis["soil_point"], {"lat": 29.364, "lon": -95.865})
            self.assertIn("soil_profile", analysis)
            self.assertIsNone(analysis["soil_profile"])
        self.assertEqual(sources.count(), 0)

    def test_without_a_soil_point_the_analysis_has_no_soil_keys(self):
        self.warm_cache()
        sources = offline(self)
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.json)
        for analysis in (response.json, self.stored(response)):
            self.assertNotIn("soil_point", analysis)
            self.assertNotIn("soil_profile", analysis)
        self.assertEqual(sources.count(), 0)

    def test_an_invalid_soil_point_is_ignored(self):
        self.warm_cache()
        sources = offline(self)
        for point in (None, "29.364,-95.865", [29.364, -95.865], {}, {"lat": 29.364},
                      {"lat": "abc", "lon": -95.865}, {"lat": 95, "lon": 0},
                      {"lat": "nan", "lon": "0"}, {"lat": True, "lon": False}):
            with self.subTest(soil_point=point):
                response = self.submit(soil_point=point)
                self.assertEqual(response.status_code, 200, response.json)
                self.assertNotIn("soil_point", response.json)
                self.assertNotIn("soil_profile", response.json)
        self.assertEqual(sources.count(), 0)

    def test_the_soil_point_does_not_change_the_scores(self):
        self.warm_cache()
        plain = self.submit().json
        with_soil = self.submit(soil_point={"lat": FB1[0], "lon": FB1[1]}).json

        def scores(analysis):
            return [(r["section_label"], r["health_score"], r["health_category"],
                     r.get("recommendations")) for r in analysis["results"]]

        self.assertEqual(scores(with_soil), scores(plain))
        self.assertEqual(with_soil["field_summary"], plain["field_summary"])

    def test_the_cache_is_read_before_the_write_transaction_opens(self):
        # soil_info may create its table lazily; doing that while submit holds
        # the SQLite write lock would wait on itself until "database is locked".
        events = []
        real_get_db, real_cached = database.get_db, soil_info.cached_soil_info

        def get_db():
            events.append("get_db")
            return real_get_db()

        def cached(*args, **kwargs):
            events.append("cache")
            return real_cached(*args, **kwargs)

        with mock.patch.object(database, "get_db", side_effect=get_db), \
                mock.patch.object(soil_info, "cached_soil_info", side_effect=cached):
            response = self.submit(soil_point={"lat": FB1[0], "lon": FB1[1]})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn("cache", events)
        self.assertEqual(events[0], "cache",
                         "submit opened its connection before the soil cache lookup")


# ---------------------------------------------------------------------------
# The Soil Profile page of the combined PDF
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
AVAIL_WIDTH = letter[0] - 1.5 * inch  # what build_combined_pdf lays out in

PROFILE_WATER2 = profile(
    status="no_data", reason_code="non_soil_map_unit",
    reason='This point is mapped as "Water" in the soil survey, which has no soil '
           'properties (water, urban land and similar areas are not described).',
    lat=WATER2[0], lon=WATER2[1], point_key="29.4910,-95.6483", map_unit="Water",
    map_unit_symbol="W", survey_area="Fort Bend County, Texas", hydrologic_group="D",
    sources=[SDA])
PROFILE_PARTIAL = profile(
    status="partial", reason_code="properties_unavailable", reason=REASON_PARTIAL,
    series="Lake Charles", component_pct=90, component_kind="Series", texture="Clay",
    sand_pct=11.4, silt_pct=37.4, clay_pct=51.2, cec=48.6, layer_top_cm=0,
    layer_bottom_cm=29, sources=[SOILWEB])
PROFILE_UNAVAILABLE = profile(status="unavailable", reason_code="unavailable",
                              reason=REASON_UNAVAILABLE)
LAB_NOTE = "Send a sample to a soil lab to measure this field's actual organic matter."


def with_soil(soil_profile, point=FB1):
    analysis = copy.deepcopy(ANALYSIS)
    analysis["soil_point"] = {"lat": point[0], "lon": point[1]}
    analysis["soil_profile"] = copy.deepcopy(soil_profile)
    return analysis


class SoilReportTests(SoilTestCase):
    """soil_report.soil_profile_flowables on its own."""

    def page_text(self, analysis):
        flow = soil_report.soil_profile_flowables(analysis, pdf_report._styles(),
                                                  AVAIL_WIDTH)
        text = " ".join(flow_text(flow).split())
        pdf_report._render(list(flow))  # it has to lay out, not just construct
        self.assertNotIn("None", text)
        return text

    def test_no_soil_point_means_no_page(self):
        for analysis in (ANALYSIS, dict(ANALYSIS, soil_profile=PROFILE_FB1),
                         dict(ANALYSIS, soil_point=None, soil_profile=None)):
            with self.subTest(keys=sorted(analysis)):
                self.assertEqual(soil_report.soil_profile_flowables(
                    analysis, pdf_report._styles(), AVAIL_WIDTH), [])

    def test_an_ok_profile_shows_organic_matter_and_the_survey_details(self):
        text = self.page_text(with_soil(PROFILE_FB1))
        for phrase in ("Soil Profile", "29.3640", "-95.8650",
                       "Typical organic matter for this soil type: 4.0%",
                       "survey range 2.0-6.0%", "0-29 cm",
                       "Lake Charles (90% of map unit)",
                       "Lake Charles clay, 0 to 1 percent slopes",
                       "Fort Bend County, Texas",
                       "Clay: 11% sand, 37% silt, 51% clay",
                       "Moderately well drained",
                       "D: very slow infiltration, high runoff potential",
                       "48.6 meq/100 g",
                       "not a measurement of this field",
                       "Send a sample to a soil lab to measure this field's actual "
                       "organic matter."):
            self.assertIn(phrase, text)

    def test_rows_without_a_value_are_left_out(self):
        text = self.page_text(with_soil(dict(PROFILE_FB1, drainage=None, cec=None,
                                             hydrologic_group=None)))
        self.assertNotIn("Drainage", text)
        self.assertNotIn("meq/100 g", text)
        self.assertNotIn("infiltration", text)
        self.assertIn("Lake Charles (90% of map unit)", text)

    def test_hydrologic_groups_get_a_plain_english_hint(self):
        hints = {"A": "high infiltration", "B": "moderate", "C": "slow infiltration",
                 "D": "very slow infiltration", "C/D": "C/D"}
        for group, hint in hints.items():
            with self.subTest(group=group):
                text = self.page_text(with_soil(dict(PROFILE_FB1, hydrologic_group=group)))
                self.assertIn(hint, text)

    def test_survey_gaps_show_the_reason(self):
        om_missing = dict(PROFILE_FB1, organic_matter_pct=None, organic_matter_low=None,
                          organic_matter_high=None, reason_code="om_not_in_survey",
                          reason=REASON_OM_NOT_RECORDED)
        cases = {"no_data": (PROFILE_WATER2, WATER2, ["Water"]),
                 "partial": (PROFILE_PARTIAL, FB1, ["Lake Charles"]),
                 "unavailable": (PROFILE_UNAVAILABLE, FB1, []),
                 "om_not_in_survey": (om_missing, FB1, ["Lake Charles"])}
        for label, (soil_profile, point, extra) in cases.items():
            with self.subTest(case=label):
                text = self.page_text(with_soil(soil_profile, point))
                self.assertIn("Soil Profile", text)
                self.assertIn(soil_profile["reason"], text)
                for phrase in extra:
                    self.assertIn(phrase, text)

    def test_a_point_without_a_profile_says_it_had_not_loaded(self):
        text = self.page_text(with_soil(None))
        self.assertIn("Soil survey data had not loaded for this field when this report "
                      "was made.", text)
        self.assertNotIn(LAB_NOTE, text)

    def test_a_water_map_unit_gets_no_soil_advice(self):
        # SDA gives the "Water" unit hydrologic group D; "very slow
        # infiltration" and a soil-lab sample make no sense for a lake.
        text = self.page_text(with_soil(PROFILE_WATER2, WATER2))
        self.assertIn("Hydrologic group D", text)
        self.assertNotIn("infiltration", text)
        self.assertNotIn(LAB_NOTE, text)

    def test_the_lab_note_goes_with_soil_values_only(self):
        for label, soil_profile, shown in (("ok", PROFILE_FB1, True),
                                           ("partial", PROFILE_PARTIAL, True),
                                           ("unavailable", PROFILE_UNAVAILABLE, False)):
            with self.subTest(status=label):
                text = self.page_text(with_soil(soil_profile))
                self.assertEqual(LAB_NOTE in text, shown)

    def test_markup_in_survey_text_is_printed_literally(self):
        hostile = dict(PROFILE_FB1, series="<b>Lake & Charles</i>",
                       map_unit='Clay <font size="40">huge</font>',
                       survey_area="A & B <br/> County", texture="<para>",
                       drainage="Well & truly drained", source_note="Note <unclosed")
        text = self.page_text(with_soil(hostile))
        self.assertIn("<b>Lake & Charles</i>", text)
        self.assertIn('Clay <font size="40">huge</font>', text)
        self.assertIn("Well & truly drained", text)

    def test_unexpected_values_do_not_break_the_page(self):
        odd = [("junk", FB1), ({"status": 7, "series": 12, "organic_matter_pct": "lots",
                                "sources": "x", "layer_top_cm": "deep"}, FB1),
               (PROFILE_FB1, ("north", None))]
        for soil_profile, point in odd:
            with self.subTest(profile=soil_profile, point=point):
                analysis = copy.deepcopy(ANALYSIS)
                analysis["soil_point"] = {"lat": point[0], "lon": point[1]}
                analysis["soil_profile"] = soil_profile
                soil_report.soil_profile_flowables(analysis, pdf_report._styles(),
                                                   AVAIL_WIDTH)
                pdf_report.build_combined_pdf(analysis)


class ReportPdfTests(SoilTestCase):
    """The combined report and its routes; neither may touch the network."""

    def setUp(self):
        super().setUp()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True
            session["admin_id"] = 1

    def add_farm(self, analysis):
        farm_id = database.execute(
            "INSERT INTO farms (farm_name, date, n_sections, mean_health_score,"
            " result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (analysis["farm_name"], analysis["date"], len(analysis["results"]), 71,
             json.dumps(analysis), database.now_iso()))
        self.addCleanup(database.execute, "DELETE FROM farms WHERE id = ?", (farm_id,))
        return farm_id

    def report(self, analysis):
        sources = offline(self)
        response = self.client.get("/api/report/%d/all" % self.add_farm(analysis))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertEqual(sources.count(), 0, "a PDF route fetched soil data")
        return response.data

    def warm_cache(self):
        fb1_sources(self)
        soil_info.get_soil_info(*FB1)

    def test_the_soil_page_adds_exactly_one_page(self):
        baseline = pdf_report.build_combined_pdf(ANALYSIS)
        with_page = pdf_report.build_combined_pdf(with_soil(PROFILE_FB1))
        self.assertEqual(page_count(with_page), page_count(baseline) + 1)
        self.assertIn("Soil Profile", pdf_text(with_page))
        self.assertIn("Typical organic matter for this soil type", pdf_text(with_page))
        self.assertNotIn("Soil Profile", pdf_text(baseline))

    def test_report_all_prints_the_stored_profile(self):
        text = pdf_text(self.report(with_soil(PROFILE_FB1)))
        self.assertIn("Soil Profile", text)
        self.assertIn("Typical organic matter for this soil type", text)
        self.assertIn("4.0%", text)

    def test_report_all_without_a_soil_point_has_no_soil_page(self):
        plain = self.report(ANALYSIS)
        with_page = self.report(with_soil(PROFILE_FB1))
        self.assertNotIn("Soil Profile", pdf_text(plain))
        self.assertEqual(page_count(plain), page_count(with_page) - 1)

    def test_report_all_fills_a_missing_profile_from_the_cache(self):
        self.warm_cache()
        text = pdf_text(self.report(with_soil(None)))
        self.assertIn("Typical organic matter for this soil type", text)
        self.assertIn("Lake Charles", text)
        self.assertNotIn("had not loaded", text)

    def test_report_all_upgrades_partial_or_unavailable_from_the_cache(self):
        self.warm_cache()
        for stored in (PROFILE_PARTIAL, PROFILE_UNAVAILABLE):
            with self.subTest(stored=stored["status"]):
                text = pdf_text(self.report(with_soil(stored)))
                self.assertIn("4.0%", text)
                self.assertNotIn("did not respond in time", text)

    def test_report_all_keeps_a_stored_answer(self):
        self.warm_cache()
        stored = dict(PROFILE_FB1, series="Beaumont",
                      map_unit="Beaumont clay, 0 to 1 percent slopes")
        text = pdf_text(self.report(with_soil(stored)))
        self.assertIn("Beaumont", text)
        self.assertNotIn("Lake Charles", text)
        text = pdf_text(self.report(with_soil(PROFILE_WATER2, WATER2)))
        self.assertIn("Water", text)

    def test_report_all_says_when_the_profile_had_not_loaded(self):
        text = pdf_text(self.report(with_soil(None)))
        self.assertIn("Soil survey data had not loaded", text)

    def test_report_all_survives_markup_in_the_stored_profile(self):
        hostile = dict(PROFILE_FB1, series="<b>Lake & Charles</i>",
                       map_unit="<font size=99>", reason="</para>")
        self.report(with_soil(hostile))

    def test_the_single_section_report_has_no_soil_page(self):
        sources = offline(self)
        farm_id = self.add_farm(with_soil(PROFILE_FB1))
        response = self.client.get("/api/report/%d/section/A1" % farm_id)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Soil Profile", pdf_text(response.data))
        self.assertEqual(sources.count(), 0)


if __name__ == "__main__":
    unittest.main()
