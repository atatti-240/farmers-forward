"""Mapped USDA soil-survey data for one point, for the Soil Profile card.

Given one coordinate (the field's centre, a Farm Finder pin or a sample point)
this looks up what the USDA NRCS soil survey (SSURGO) maps there: the soil
series, its typical surface organic matter, texture, drainage and so on. These
are typical values for the soil type, NOT a measurement of the field, and
nothing here feeds the crop scoring. The results page and the combined PDF
report only display them.

Two services are asked at the same time:
  * UC Davis SoilWeb (the landPKS API) supplies the soil series name, which is
    the source the user asked for. It has no organic-matter field.
  * USDA Soil Data Access (SDA) runs one SQL query (SDA_SQL) that returns the
    organic matter, the other surface-layer properties and the map unit, plus a
    data_status saying exactly why data is missing. SoilWeb answers open water,
    a "Water" map unit and "Urban land" with the same empty response, so SDA
    decides the outcome.

Both requests share ONE wall-clock budget, TIMEOUT_SECONDS. urlopen's timeout
applies to each socket operation, not to the whole request (an SDA call made
with timeout=10 once took 10.3 s and succeeded), so each source runs on its own
daemon thread and is abandoned, and counted as failed, when the budget is spent.
_http_json also stops reading a reply once that time is up, so an abandoned
thread does not linger on a server that trickles bytes.

A definitive SDA answer is cached by rounded point in soil_info_cache for
CACHE_MAX_AGE_DAYS: survey data is refreshed about once a year. Anything short
of that (SDA down or slow) is never cached, so the next request tries again.
The table is created here, lazily, so database.py's schema stays untouched.
The cache write runs on its own thread and the lookup waits for it only
briefly (CACHE_WRITE_WAIT_SECONDS), so a locked SQLite file cannot stretch the
lookup past its budget.

get_soil_info() never raises for network, database or parsing problems. Only
bad coordinates raise ValueError, which the route answers with a 400.
"""

from __future__ import annotations

import json
import logging
import math
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from backend import database

log = logging.getLogger(__name__)

# Both services get a named client. urllib's default "Python-urllib/3.x" is a
# known bot signature, and Render's egress has been Cloudflare-flagged with it.
USER_AGENT = ("FarmersForward/1.0 (soil-profile lookup; "
              "+https://github.com/atatti-240/farmers-forward)")

# Wall-clock budget for one whole lookup, both sources together.
TIMEOUT_SECONDS = 10.0

SOILWEB_URL = "https://casoilresource.lawr.ucdavis.edu/api/landPKS.php"
SDA_URL = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"

CACHE_MAX_AGE_DAYS = 365

# How long a lookup waits for its cache write before answering anyway. On
# SQLite the write can sit in sqlite3's 5 s busy timeout while another request
# (an /api/submit, say) holds the write lock; it keeps trying on its own thread
# after the route has answered, so the answer is usually still cached.
CACHE_WRITE_WAIT_SECONDS = 0.5

# Neither service sends more than a few tens of KB for one point. A longer body
# is refused, which counts as a failure.
MAX_RESPONSE_BYTES = 2_000_000
_READ_CHUNK_BYTES = 65536

SOILWEB_NAME = "UC Davis SoilWeb"
SDA_NAME = "USDA Soil Data Access"

# One row for the map unit under the point. `dom_*` is the dominant component;
# `s` is the component the profile is built from (the dominant one unless it
# has no surface organic matter recorded, then the largest one that does). The
# surface layer skips O (organic litter) horizons. data_status is one of
# dominant | fallback | no_surface_om | no_components | no_mukey. Formatted with
# str.format, so it holds exactly two brace pairs: {lon} and {lat}.
SDA_SQL = """\
WITH pt AS (
    SELECT DISTINCT mukey
    FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({lon} {lat})')
),
comp AS (
    SELECT c.mukey, c.cokey, c.compname, c.comppct_r, c.compkind, c.majcompflag,
           c.drainagecl, c.hydgrp, c.taxclname,
           h.chkey, h.hzname, h.hzdept_r, h.hzdepb_r, h.om_l, h.om_r, h.om_h,
           h.sandtotal_r, h.silttotal_r, h.claytotal_r, h.cec7_r,
           CASE WHEN h.om_r IS NULL THEN 0 ELSE 1 END AS has_om
    FROM pt
    INNER JOIN component AS c ON c.mukey = pt.mukey
    OUTER APPLY (
        SELECT TOP 1 ch.chkey, ch.hzname, ch.hzdept_r, ch.hzdepb_r, ch.om_l, ch.om_r, ch.om_h,
               ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r, ch.cec7_r
        FROM chorizon AS ch
        WHERE ch.cokey = c.cokey AND (ch.desgnmaster IS NULL OR ch.desgnmaster <> 'O')
        ORDER BY CASE WHEN ch.hzdept_r IS NULL THEN 1 ELSE 0 END, ch.hzdept_r, ch.chkey
    ) AS h
),
ranked AS (
    SELECT comp.*,
           ROW_NUMBER() OVER (PARTITION BY mukey ORDER BY comppct_r DESC,
               CASE WHEN majcompflag = 'Yes' THEN 0 ELSE 1 END, has_om DESC, cokey) AS dom_rank,
           ROW_NUMBER() OVER (PARTITION BY mukey ORDER BY has_om DESC, comppct_r DESC,
               CASE WHEN majcompflag = 'Yes' THEN 0 ELSE 1 END, cokey) AS pick_rank
    FROM comp
)
SELECT TOP 1
    COUNT(pt.mukey) OVER () AS n_mukeys,
    pt.mukey, mu.musym, mu.muname, l.areasymbol, l.areaname,
    d.compname AS dom_compname, d.comppct_r AS dom_comppct_r, d.compkind AS dom_compkind,
    s.cokey, s.compname, s.comppct_r, s.compkind, s.majcompflag,
    s.drainagecl, s.hydgrp, s.taxclname,
    s.chkey, s.hzname, s.hzdept_r, s.hzdepb_r, s.om_l, s.om_r, s.om_h,
    s.sandtotal_r, s.silttotal_r, s.claytotal_r, s.cec7_r,
    tx.texture, tx.texdesc,
    CASE WHEN pt.mukey IS NULL THEN 'no_mukey'
         WHEN s.cokey IS NULL THEN 'no_components'
         WHEN s.has_om = 0 THEN 'no_surface_om'
         WHEN s.cokey = d.cokey THEN 'dominant'
         ELSE 'fallback' END AS data_status
FROM (SELECT 1 AS anchor) AS a
LEFT JOIN pt ON 1 = 1
LEFT JOIN mapunit AS mu ON mu.mukey = pt.mukey
LEFT JOIN legend AS l ON l.lkey = mu.lkey
LEFT JOIN ranked AS d ON d.mukey = pt.mukey AND d.dom_rank = 1
LEFT JOIN ranked AS s ON s.mukey = pt.mukey AND s.pick_rank = 1
OUTER APPLY (
    SELECT TOP 1 tg.texture, tg.texdesc
    FROM chtexturegrp AS tg
    WHERE tg.chkey = s.chkey AND tg.rvindicator = 'Yes'
    ORDER BY tg.chtgkey
) AS tx
ORDER BY CASE WHEN s.has_om = 1 THEN 0 ELSE 1 END, pt.mukey
"""

_SDA_STATUSES = ("dominant", "fallback", "no_surface_om", "no_components", "no_mukey")

# SSURGO's component kind for water, urban land, pits and the like.
_MISC_AREA = "miscellaneous area"

_REASON_NO_MUKEY = ("No soil survey map unit at this point (open water, or "
                    "outside USDA soil survey coverage).")
_REASON_NO_COMPONENTS = ("The soil survey maps this point but does not describe "
                         "the soil there.")
_REASON_NO_OM = ("The soil survey does not record organic matter for this "
                 "soil's surface layer.")
_REASON_PARTIAL = ("The USDA soil survey service did not respond in time, so "
                   "organic matter and drainage are missing. Try again in a minute.")
_REASON_EMPTY = ("No soil survey data at this point (open water, urban land, or "
                 "outside survey coverage).")
_REASON_UNAVAILABLE = ("The soil survey services did not respond in time. The "
                       "assessment is not affected; try again later.")

# Every payload has exactly these keys, whatever its status.
_PAYLOAD_KEYS = (
    "status", "reason_code", "reason", "lat", "lon", "point_key",
    "series", "component_pct", "component_kind",
    "map_unit", "map_unit_symbol", "survey_area",
    "texture", "sand_pct", "silt_pct", "clay_pct", "cec",
    "drainage", "hydrologic_group",
    "organic_matter_pct", "organic_matter_low", "organic_matter_high",
    "layer_top_cm", "layer_bottom_cm",
    "sources", "source_note", "fetched_at", "cached",
)
_STATUSES = ("ok", "partial", "no_data", "unavailable")


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------
def _coordinate(value: Any, name: str, limit: int) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("%s is required" % name)
    # bool is an int subclass; True is not a latitude.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("%s must be a number" % name)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("%s must be a number" % name) from None
    if not math.isfinite(number):
        raise ValueError("%s must be a number" % name)
    if not -limit <= number <= limit:
        raise ValueError("%s must be between -%d and %d" % (name, limit, limit))
    return number


def parse_coords(lat: Any, lon: Any) -> Tuple[float, float]:
    """Validated (lat, lon) floats from strings or numbers; ValueError if unusable."""
    return _coordinate(lat, "lat", 90), _coordinate(lon, "lon", 180)


def _rounded_point(lat: Any, lon: Any) -> Tuple[float, float]:
    """The point actually queried: 4 decimals (about 11 m), never -0.0."""
    lat, lon = parse_coords(lat, lon)
    # Adding 0.0 turns -0.0 into 0.0, so the key never reads "-0.0000".
    return round(lat, 4) + 0.0, round(lon, 4) + 0.0


def point_key(lat: Any, lon: Any) -> str:
    """Cache key for a point, e.g. "29.3640,-95.8650"."""
    lat, lon = _rounded_point(lat, lon)
    return "%.4f,%.4f" % (lat, lon)


# ---------------------------------------------------------------------------
# Value helpers. SDA sends every value as a string; SoilWeb sends numbers.
# ---------------------------------------------------------------------------
def _text(value: Any) -> Optional[str]:
    """A stripped string, or None for null/empty (SDA uses '' for missing)."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool):
        return None
    text = _text(value)
    if text is None:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _rounded(value: Any, places: str) -> Optional[Decimal]:
    # ROUND_HALF_UP on the original digits: the survey's "1.15" is 1.2, where
    # float round(1.15, 1) gives 1.1 because 1.15 is 1.1499... in binary.
    number = _decimal(value)
    if number is None:
        return None
    try:
        return number.quantize(Decimal(places), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _one_decimal(value: Any) -> Optional[float]:
    number = _rounded(value, "0.1")
    return None if number is None else float(number) + 0.0


def _whole(value: Any) -> Optional[int]:
    number = _rounded(value, "1")
    return None if number is None else int(number)


def _same_name(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _key_order(value: Any) -> Tuple[int, Any]:
    """Sort key for an id that may arrive as an int or a numeric string."""
    text = _text(value)
    if text is None:
        return (2, "")
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _read_body(response: Any, deadline: float) -> bytes:
    """Up to MAX_RESPONSE_BYTES of a reply's body, read before `deadline`.

    read1 returns whatever one socket read brings (read(n) would wait for all
    n bytes), so the clock is checked between reads. urlopen's timeout is per
    read, so without this a server trickling a byte at a time would keep an
    abandoned source thread, and its socket, alive for hours.
    """
    body = bytearray()
    while len(body) <= MAX_RESPONSE_BYTES:
        chunk = response.read1(min(_READ_CHUNK_BYTES, MAX_RESPONSE_BYTES + 1 - len(body)))
        if not chunk:
            return bytes(body)
        body += chunk
        if time.monotonic() > deadline:
            raise socket.timeout("soil lookup ran out of time reading the reply")
    raise ValueError("reply is longer than %d bytes" % MAX_RESPONSE_BYTES)


def _http_json(url: str, data: Optional[bytes] = None,
               timeout: float = TIMEOUT_SECONDS) -> Any:
    """GET (or POST `data` as JSON) and parse the JSON reply. Errors propagate.

    The only caller of urlopen in this module. `timeout` bounds each socket
    operation, and reading the body stops once `timeout` seconds have passed
    in all; _lookup enforces the overall deadline on top of that.
    """
    deadline = time.monotonic() + timeout
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers,
        method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        # A real HTTPResponse has read1; a bare stand-in is read in one go.
        if callable(getattr(type(response), "read1", None)):
            raw = _read_body(response, deadline)
        else:
            raw = response.read(MAX_RESPONSE_BYTES)
    return json.loads(raw.decode("utf-8-sig"))


def _fetch_soilweb(lat: float, lon: float, timeout: float) -> Dict[str, Any]:
    """The raw landPKS reply for the map unit under the point.

    r=1 limits it to that map unit (rows with which == 'a') plus neighbours; q
    is required but ignored.
    """
    query = urllib.parse.urlencode([
        ("q", "spn"), ("lat", "%.4f" % lat), ("lon", "%.4f" % lon), ("r", "1")])
    raw = _http_json("%s?%s" % (SOILWEB_URL, query), timeout=timeout)
    if not isinstance(raw, dict):
        raise ValueError("SoilWeb reply is not a JSON object")
    return raw


def _fetch_sda(lat: float, lon: float, timeout: float) -> Dict[str, Optional[str]]:
    """SDA_SQL's single row as {column name: raw string or None}.

    SDA answers a query that fails (or returns no rows) with "{}", so a reply
    without a header row plus one data row is a failure, not "no data". The
    query always returns one row when it works; data_status carries the
    "nothing here" cases.
    """
    body = json.dumps({
        "query": SDA_SQL.format(lon="%.4f" % lon, lat="%.4f" % lat),
        "format": "JSON+COLUMNNAME",
    }).encode("utf-8")
    raw = _http_json(SDA_URL, data=body, timeout=timeout)
    table = raw.get("Table") if isinstance(raw, dict) else None
    if not isinstance(table, list) or len(table) < 2:
        raise ValueError("Soil Data Access returned no rows")
    header, row = table[0], table[1]
    if (not isinstance(header, list) or not isinstance(row, list)
            or len(header) != len(row) or "data_status" not in header):
        raise ValueError("Soil Data Access reply has an unexpected shape")
    return {str(name): (None if value is None else str(value))
            for name, value in zip(header, row)}


# ---------------------------------------------------------------------------
# Parsing + combining
# ---------------------------------------------------------------------------
def _rows(raw: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    rows = raw.get(name)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _not_organic(row: Dict[str, Any]) -> bool:
    # Matches SDA_SQL, which skips O (organic litter) horizons.
    return not (_text(row.get("hzname")) or "").startswith("O")


def _parse_soilweb(raw: Any) -> Dict[str, Any]:
    """The dominant component of the map unit under the point, from landPKS.

    has_data is False when SoilWeb has no map unit there; it does not say why
    (water, urban land and no coverage all look the same).
    """
    if not isinstance(raw, dict):
        raise ValueError("SoilWeb reply is not a JSON object")
    parsed: Dict[str, Any] = {
        "series": None, "cokey": None, "component_pct": None,
        "component_kind": None, "texture": None, "sand_pct": None,
        "silt_pct": None, "clay_pct": None, "cec": None,
        "layer_top_cm": None, "layer_bottom_cm": None, "has_data": False,
    }

    # 'b' rows are neighbouring map units; only 'a' is under the point.
    components = [row for row in _rows(raw, "spn")
                  if _text(row.get("which")) == "a" and _text(row.get("compname"))]
    if not components:
        return parsed

    def rank(row):
        pct = _decimal(row.get("comppct_r"))
        major = _text(row.get("majcompflag")) == "Yes"  # 'No ' has a trailing space
        return (-(pct if pct is not None else Decimal(-1)), 0 if major else 1,
                _key_order(row.get("cokey")))

    dominant = min(components, key=rank)
    name = _text(dominant.get("compname"))
    cokey = _text(dominant.get("cokey"))
    parsed.update(
        has_data=True,
        # SoilWeb lowercases names: 'lake charles' -> 'Lake Charles'.
        series=name.title(),
        cokey=cokey,
        component_pct=_whole(dominant.get("comppct_r")),
        component_kind=_text(dominant.get("compkind")),
    )

    # Minor components carry one all-null placeholder horizon, hence the
    # hzname/depth filter.
    layers = [row for row in _rows(raw, "hz")
              if _text(row.get("cokey")) == cokey and _text(row.get("hzname"))
              and _decimal(row.get("hzdept_r")) is not None and _not_organic(row)]
    if layers:
        top = min(layers, key=lambda row: (_decimal(row.get("hzdept_r")),
                                           _key_order(row.get("chkey"))))
        parsed.update(
            sand_pct=_one_decimal(top.get("sandtotal_r")),
            silt_pct=_one_decimal(top.get("silttotal_r")),
            clay_pct=_one_decimal(top.get("claytotal_r")),
            cec=_one_decimal(top.get("cec7_r")),
            layer_top_cm=_whole(top.get("hzdept_r")),
            layer_bottom_cm=_whole(top.get("hzdepb_r")),
        )

    # The series' typical pedon, used only when SDA has no texture.
    pedon = [row for row in _rows(raw, "OSD_morph")
             if _same_name(_text(row.get("series")), name)
             and _text(row.get("texture_class"))
             and _decimal(row.get("top")) is not None and _not_organic(row)]
    if pedon:
        texture = _text(min(pedon, key=lambda row: _decimal(row.get("top")))
                        .get("texture_class"))
        parsed["texture"] = texture[:1].upper() + texture[1:]
    return parsed


def _blank_payload() -> Dict[str, Any]:
    payload: Dict[str, Any] = {key: None for key in _PAYLOAD_KEYS}
    payload["sources"] = []
    payload["cached"] = False
    return payload


def _source_note(sources: List[str], has_values: bool,
                 texture_from_soilweb: bool = False) -> str:
    if SOILWEB_NAME in sources and SDA_NAME in sources and texture_from_soilweb:
        via = ("; soil series and texture via UC Davis SoilWeb, organic matter "
               "and other soil properties via USDA Soil Data Access")
    elif SOILWEB_NAME in sources and SDA_NAME in sources:
        via = ("; soil series via UC Davis SoilWeb, organic matter and soil "
               "properties via USDA Soil Data Access")
    elif SDA_NAME in sources:
        via = " via USDA Soil Data Access"
    elif SOILWEB_NAME in sources:
        via = " via UC Davis SoilWeb"
    else:
        via = ""
    if has_values:
        return ("Mapped soil survey data (USDA NRCS SSURGO%s). These are typical "
                "values for this soil type, not a measurement of this field." % via)
    return "Mapped soil survey data (USDA NRCS SSURGO%s), not a measurement of this field." % via


def _finish(payload: Dict[str, Any], status: str, reason_code: Optional[str],
            reason: Optional[str], sources: List[str],
            texture_from_soilweb: bool = False) -> Dict[str, Any]:
    # Fixed order, whichever source was added first.
    sources = [name for name in (SOILWEB_NAME, SDA_NAME) if name in sources]
    payload.update(
        status=status, reason_code=reason_code, reason=reason, sources=sources,
        source_note=_source_note(sources, status in ("ok", "partial"),
                                 texture_from_soilweb),
        fetched_at=database.now_iso())
    return payload


def _non_soil_reason(map_unit: Optional[str]) -> str:
    where = '"%s"' % map_unit if map_unit else "a non-soil area"
    return ("This point is mapped as %s in the soil survey, which has no soil "
            "properties (water, urban land and similar areas are not described)."
            % where)


def _from_sda(payload: Dict[str, Any], sda: Dict[str, Optional[str]],
              data_status: str, soilweb: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload.update(map_unit=_text(sda.get("muname")),
                   map_unit_symbol=_text(sda.get("musym")),
                   survey_area=_text(sda.get("areaname")))
    if data_status == "no_mukey":
        return _finish(payload, "no_data", "no_survey_coverage", _REASON_NO_MUKEY,
                       [SDA_NAME])
    if data_status == "no_components":
        return _finish(payload, "no_data", "no_components", _REASON_NO_COMPONENTS,
                       [SDA_NAME])

    kinds = {(_text(sda.get("compkind")) or "").casefold(),
             (_text(sda.get("dom_compkind")) or "").casefold()}
    if data_status == "no_surface_om" and (_text(sda.get("chkey")) is None
                                           or _MISC_AREA in kinds):
        # Water, urban land and the like: a map unit, but no soil to describe.
        payload["hydrologic_group"] = _text(sda.get("hydgrp"))
        return _finish(payload, "no_data", "non_soil_map_unit",
                       _non_soil_reason(payload["map_unit"]), [SDA_NAME])

    # A described soil. Every property comes from the one selected component,
    # so the profile never mixes two soils; SoilWeb confirms the series name.
    sources = [SDA_NAME]
    sda_name = _text(sda.get("compname"))
    web_name = soilweb.get("series") if soilweb and soilweb.get("has_data") else None
    web_matches = _same_name(web_name, sda_name)
    if web_matches:
        sources.append(SOILWEB_NAME)
    elif web_name and sda_name and not _same_name(web_name, _text(sda.get("dom_compname"))):
        log.warning("Soil series disagree: SoilWeb %r, Soil Data Access %r (map unit %r)",
                    web_name, sda_name, payload["map_unit"])
    # On a match the names are equal; SDA's spelling keeps capitals such as
    # "McAllen" that SoilWeb's lowercase name loses.
    series = sda_name
    if series is None and web_name:
        series, web_matches = web_name, True
        sources.append(SOILWEB_NAME)

    texture = _text(sda.get("texdesc"))
    # SoilWeb's texture is the series' typical pedon, so the note credits it.
    texture_from_soilweb = False
    if texture is None and web_matches and soilweb.get("texture"):
        texture = soilweb["texture"]
        texture_from_soilweb = True
        sources.append(SOILWEB_NAME)

    payload.update(
        series=series,
        component_pct=_whole(sda.get("comppct_r")),
        component_kind=_text(sda.get("compkind")),
        texture=texture,
        sand_pct=_one_decimal(sda.get("sandtotal_r")),
        silt_pct=_one_decimal(sda.get("silttotal_r")),
        clay_pct=_one_decimal(sda.get("claytotal_r")),
        cec=_one_decimal(sda.get("cec7_r")),
        drainage=_text(sda.get("drainagecl")),
        hydrologic_group=_text(sda.get("hydgrp")),
        organic_matter_pct=_one_decimal(sda.get("om_r")),
        organic_matter_low=_one_decimal(sda.get("om_l")),
        organic_matter_high=_one_decimal(sda.get("om_h")),
        layer_top_cm=_whole(sda.get("hzdept_r")),
        layer_bottom_cm=_whole(sda.get("hzdepb_r")),
    )
    if payload["organic_matter_pct"] is None:
        return _finish(payload, "ok", "om_not_in_survey", _REASON_NO_OM, sources,
                       texture_from_soilweb)
    return _finish(payload, "ok", None, None, sources, texture_from_soilweb)


def _combine(soilweb: Optional[Dict[str, Any]], sda: Optional[Dict[str, Optional[str]]],
             soilweb_error: Optional[str] = None,
             sda_error: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Build the payload from whatever each source returned (None = failed).

    Returns (payload, cacheable). Only a definitive SDA answer is cacheable;
    a SoilWeb failure alone does not prevent it. lat, lon and point_key are
    left for the caller. The *_error arguments are descriptions for the log.
    """
    payload = _blank_payload()
    data_status = _text(sda.get("data_status")) if isinstance(sda, dict) else None
    if isinstance(sda, dict) and data_status not in _SDA_STATUSES:
        log.warning("Soil Data Access returned data_status %r; treating it as a failure",
                    data_status)
        data_status = None
    if data_status is not None:
        return _from_sda(payload, sda, data_status, soilweb), True

    # SDA failed or timed out (sda_error says how). Nothing below is cached.
    if soilweb is not None and soilweb.get("has_data"):
        for key in ("series", "component_pct", "component_kind", "texture",
                    "sand_pct", "silt_pct", "clay_pct", "cec",
                    "layer_top_cm", "layer_bottom_cm"):
            payload[key] = soilweb.get(key)
        return _finish(payload, "partial", "properties_unavailable", _REASON_PARTIAL,
                       [SOILWEB_NAME]), False
    if soilweb is not None:
        return _finish(payload, "no_data", "no_survey_coverage", _REASON_EMPTY,
                       [SOILWEB_NAME]), False
    return _finish(payload, "unavailable", "unavailable", _REASON_UNAVAILABLE, []), False


# ---------------------------------------------------------------------------
# Running both sources under one deadline
# ---------------------------------------------------------------------------
class _SourceJob:
    """One source fetched on a daemon thread, so a hung server can be abandoned.

    Daemon threads rather than a ThreadPoolExecutor: a pool's workers are joined
    at interpreter exit and a few hung calls would fill it. An abandoned daemon
    thread ends on its own: a silent server within one socket timeout, and one
    that keeps trickling bytes as soon as _http_json's own clock runs out.
    """

    def __init__(self, name: str, fetch, timeout: float):
        self.value = None
        self.error: Optional[str] = None
        self._thread = threading.Thread(target=self._run, args=(fetch, timeout),
                                        name="soil-info-%s" % name, daemon=True)
        self._thread.start()

    def _run(self, fetch, timeout: float) -> None:
        try:
            self.value = fetch(timeout)
        # Everything, including a bare socket.timeout (Python 3.9 raises that,
        # not URLError, for a slow read): a source failure is just recorded.
        except Exception as exc:  # noqa: BLE001
            self.error = "%s: %s" % (type(exc).__name__, exc)

    def result(self, deadline: float) -> Tuple[Any, Optional[str]]:
        """(value, None) on success, else (None, what went wrong)."""
        self._thread.join(max(0.0, deadline - time.monotonic()))
        if self._thread.is_alive():
            return None, "timeout"
        if self.error is not None or self.value is None:
            return None, self.error or "no result"
        return self.value, None


def _lookup(lat: float, lon: float, key: str) -> Tuple[Dict[str, Any], bool]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    soilweb_job = _SourceJob(
        "soilweb", lambda timeout: _parse_soilweb(_fetch_soilweb(lat, lon, timeout)),
        TIMEOUT_SECONDS)
    sda_job = _SourceJob(
        "sda", lambda timeout: _fetch_sda(lat, lon, timeout),
        max(0.1, deadline - time.monotonic()))
    soilweb, soilweb_error = soilweb_job.result(deadline)
    sda, sda_error = sda_job.result(deadline)
    for name, error in ((SOILWEB_NAME, soilweb_error), (SDA_NAME, sda_error)):
        if error:
            log.warning("Soil lookup at %s: %s failed (%s)", key, name, error)
    return _combine(soilweb, sda, soilweb_error, sda_error)


# ---------------------------------------------------------------------------
# Cache (soil_info_cache, created on first use)
# ---------------------------------------------------------------------------
_CACHE_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS soil_info_cache (
    id         INTEGER PRIMARY KEY,
    point_key  TEXT NOT NULL UNIQUE,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL
)
"""

# The identity id makes the RETURNING id that database.py appends to every
# INSERT valid here too.
_CACHE_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS soil_info_cache (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    point_key  TEXT NOT NULL UNIQUE,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL
)
"""

_CACHE_UPSERT = (
    "INSERT INTO soil_info_cache (point_key, payload, fetched_at) VALUES (?, ?, ?) "
    "ON CONFLICT (point_key) DO UPDATE SET payload = excluded.payload, "
    "fetched_at = excluded.fetched_at")

# Databases whose table already exists, keyed by database.describe_backend(),
# so a process that is re-pointed at another database (the tests do this)
# still creates it there.
_tables_ready = set()
_tables_lock = threading.Lock()


def _ensure_table() -> None:
    target = database.describe_backend()
    if target in _tables_ready:
        return
    with _tables_lock:
        if target in _tables_ready:
            return
        conn = database.get_db()
        try:
            conn.execute(_CACHE_SCHEMA_POSTGRES if database.using_postgres()
                         else _CACHE_SCHEMA_SQLITE)
            conn.commit()
        finally:
            conn.close()
        _tables_ready.add(target)


def _parse_iso(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    """The fresh cached payload for a point key, or None. Never raises."""
    try:
        _ensure_table()
        row = database.query_one(
            "SELECT payload, fetched_at FROM soil_info_cache WHERE point_key = ?",
            (key,))
    except Exception:  # noqa: BLE001 - the cache is an optimisation only
        log.warning("Soil cache read failed for %s", key, exc_info=True)
        return None
    if row is None:
        return None
    try:
        age = datetime.now(timezone.utc) - _parse_iso(row["fetched_at"])
        if age > timedelta(days=CACHE_MAX_AGE_DAYS):
            return None
        stored = json.loads(row["payload"])
    except (TypeError, ValueError):
        log.warning("Soil cache row for %s is unreadable; ignoring it", key)
        return None
    if not isinstance(stored, dict) or stored.get("status") not in _STATUSES:
        return None
    payload = _blank_payload()
    payload.update((name, stored[name]) for name in _PAYLOAD_KEYS if name in stored)
    payload["cached"] = True
    return payload


def _cache_put(key: str, payload: Dict[str, Any]) -> None:
    """Store a payload (without its cached flag). Never raises."""
    stored = {name: value for name, value in payload.items() if name != "cached"}
    try:
        _ensure_table()
        database.execute(_CACHE_UPSERT,
                         (key, json.dumps(stored), stored.get("fetched_at")
                          or database.now_iso()))
    except Exception:  # noqa: BLE001 - the cache is an optimisation only
        log.warning("Soil cache write failed for %s", key, exc_info=True)


def _cache_put_soon(key: str, payload: Dict[str, Any]) -> None:
    """_cache_put on a daemon thread, waited on for CACHE_WRITE_WAIT_SECONDS.

    Normally the write is done long before that. When another request holds
    SQLite's write lock it carries on in the background (inside sqlite3's busy
    timeout) instead of holding up this lookup.
    """
    writer = threading.Thread(target=_cache_put, args=(key, dict(payload)),
                              name="soil-info-cache", daemon=True)
    try:
        writer.start()
    except RuntimeError:  # no thread to spare: write it here instead
        _cache_put(key, payload)
        return
    writer.join(CACHE_WRITE_WAIT_SECONDS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_soil_info(lat: Any, lon: Any) -> Dict[str, Any]:
    """Soil-survey payload for a point: from the cache if fresh, else live.

    Raises ValueError only for bad coordinates. Network, database and parsing
    problems come back as a payload (status "unavailable" at worst).
    """
    lat, lon = _rounded_point(lat, lon)
    key = "%.4f,%.4f" % (lat, lon)
    try:
        hit = _cache_get(key)
        if hit is not None:
            return hit
        payload, cacheable = _lookup(lat, lon, key)
    except Exception:  # noqa: BLE001 - the soil card must never 500 the route
        log.exception("Soil lookup at %s failed unexpectedly", key)
        payload = _finish(_blank_payload(), "unavailable", "unavailable",
                          _REASON_UNAVAILABLE, [])
        cacheable = False
    payload.update(lat=lat, lon=lon, point_key=key, cached=False)
    if cacheable:
        _cache_put_soon(key, payload)
    return payload


def cached_soil_info(lat: Any, lon: Any) -> Optional[Dict[str, Any]]:
    """The fresh cached payload for a point, or None. Never touches the network.

    For the submit and PDF routes, which must not wait on the soil services.
    None also covers invalid coordinates and any database error.
    """
    try:
        lat, lon = _rounded_point(lat, lon)
        return _cache_get("%.4f,%.4f" % (lat, lon))
    except Exception:  # noqa: BLE001 - optional data, never an error
        return None
