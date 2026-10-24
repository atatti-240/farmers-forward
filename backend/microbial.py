"""Soil respiration (24-hour CO2 burst): one optional lab value and its rating.

A lab's CO2-burst test (Haney, Solvita and the like) measures the CO2-C a dried
soil gives off in the 24 hours after it is rewetted, a common stand-in for
microbial activity. A farmer can enter ONE value for the whole field, and
rate() places it in the bands of data/soil_respiration_bands.json. It is
display only: the results page and the combined PDF report show the rating,
and nothing here feeds the health score or the crop scoring.

The bands are a published lab guideline (Ward Laboratories' Haney Test
Interpretation Guide), not thresholds calibrated for Texas soils, so the
config carries its citation and caveats and every rating carries them too. A
config marked "placeholder" gets every band label prefixed PLACEHOLDER.

The file is read on first use, never at import, and then cached until it
changes on disk.

Public API:
  * parse_respiration(value) -> Optional[float]  (None = not provided)
  * rate(value) -> dict          (an "unrated" payload if the config is bad)
  * load_config() -> dict        (ValueError if the file is missing or invalid)
  * range_text(bands, index) -> str              ("31-50", "above 200")
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

BANDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                          "soil_respiration_bands.json")

# A typo guard, well above anything real: the guide says readings run from
# near 0 to about 1000 ppm.
MAX_RESPIRATION = 5000.0

UNITS = "ppm CO2-C"
METRIC = "Soil respiration (24-hour CO2 burst)"
NOT_PROVIDED_MESSAGE = "Microbial activity: in-house test in development"
UNRATED_MESSAGE = "Microbial activity rating is unavailable right now."
PLACEHOLDER_PREFIX = "PLACEHOLDER "

_RANGE_ERROR = "soil_respiration must be a number from 0 to %d (%s)" % (
    MAX_RESPIRATION, UNITS)

# Plain ASCII decimal text such as "45", "45.5", ".5" or "1e3", the same as the
# form accepts. float() alone would also read "4_5" as 45 and Arabic-Indic
# digits as 12, so a typo could be rated as a different number.
_DECIMAL = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")

# The last config read: ((path, mtime_ns, size), validated config). Keyed by
# path so a process re-pointed at another file (the tests do this) reads that
# one, and by mtime and size so an edited file is picked up without a restart.
_cache: Optional[Tuple[Tuple[str, int, int], Dict[str, Any]]] = None
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------
def _text(value: Any) -> Optional[str]:
    """A trimmed, non-empty string, or None."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _is_number(value: Any) -> bool:
    # bool is an int subclass; True is not a band edge.
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _bound(value: Any) -> Any:
    """A band edge as the table prints it: 10.0 -> 10, 10.5 stays, None stays."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def range_text(bands: List[Dict[str, Any]], index: int) -> str:
    """How the published table reads for one band: "31-50", or "above 200".

    The open-ended last band reads "above <the previous band's max>", which is
    how the guide's text puts it (its table prints ">201").
    """
    band = bands[index]
    if band.get("max") is None:
        if index > 0:
            return "above %s" % _bound(bands[index - 1]["max"])
        return "%s and above" % _bound(band["min"])
    return "%s-%s" % (_bound(band["min"]), _bound(band["max"]))


# ---------------------------------------------------------------------------
# The submitted value
# ---------------------------------------------------------------------------
def parse_respiration(value: Any) -> Optional[float]:
    """The submitted soil respiration as a float, or None when none was given.

    None (or a missing key) and a blank string mean "not provided". Numbers and
    plain decimal strings are accepted; anything else, true/false, NaN, infinity and
    values outside 0 to MAX_RESPIRATION raise ValueError with a message fit for
    the API's 400.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        if not _DECIMAL.fullmatch(value.strip()):
            raise ValueError(_RANGE_ERROR)
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(_RANGE_ERROR)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(_RANGE_ERROR) from None
    if not math.isfinite(number) or not 0 <= number <= MAX_RESPIRATION:
        raise ValueError(_RANGE_ERROR)
    return number + 0.0  # -0.0 reads as 0.0


# ---------------------------------------------------------------------------
# The band config
# ---------------------------------------------------------------------------
def _validate(config: Any) -> Dict[str, Any]:
    """The config itself if it can rate a value; ValueError saying why not."""
    if not isinstance(config, dict):
        raise ValueError("the config is not a JSON object")
    bands = config.get("bands")
    if not isinstance(bands, list) or not bands:
        raise ValueError("bands must be a non-empty list")

    previous_max = None
    for index, band in enumerate(bands):
        if not isinstance(band, dict):
            raise ValueError("band %d is not an object" % index)
        if _text(band.get("label")) is None:
            raise ValueError("band %d has no label" % index)
        low, high = band.get("min"), band.get("max")
        if not _is_number(low):
            raise ValueError("band %d: min must be a number" % index)
        if index == 0 and low != 0:
            raise ValueError("the first band must start at 0")
        if index == len(bands) - 1:
            if high is not None:
                raise ValueError("the last band's max must be null (no upper limit)")
            continue
        if not _is_number(high):
            raise ValueError("band %d: max must be a number; only the last band's "
                             "max is null" % index)
        if high < low:
            raise ValueError("band %d: max is below its min" % index)
        if previous_max is not None and high <= previous_max:
            raise ValueError("band %d: the maxima must ascend" % index)
        previous_max = high

    source = config.get("source")
    if (not isinstance(source, dict) or _text(source.get("title")) is None
            or _text(source.get("url")) is None):
        raise ValueError("source needs a title and a url")
    if not isinstance(config.get("placeholder", False), bool):
        raise ValueError("placeholder must be true or false")
    return config


def load_config() -> Dict[str, Any]:
    """The validated band config from BANDS_PATH, read once and then cached.

    Raises ValueError, naming the file, when it cannot be read, is not JSON or
    fails validation; a failure is not cached, so the next call tries again.
    The returned dict is shared: treat it as read-only.
    """
    global _cache
    path = BANDS_PATH
    with _cache_lock:
        try:
            stat = os.stat(path)
            stamp = (path, stat.st_mtime_ns, stat.st_size)
            if _cache is not None and _cache[0] == stamp:
                return _cache[1]
            with open(path, encoding="utf-8") as fh:
                config = _validate(json.load(fh))
        except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
            raise ValueError("soil respiration bands in %s: %s" % (path, exc)) from exc
        _cache = (stamp, config)
        return config


# ---------------------------------------------------------------------------
# Rating
# ---------------------------------------------------------------------------
def _source(source: Dict[str, Any]) -> Dict[str, Any]:
    year = source.get("year")
    if isinstance(year, bool) or not isinstance(year, (int, str)):
        year = None
    return {"title": _text(source.get("title")), "publisher": _text(source.get("publisher")),
            "year": year, "url": _text(source.get("url"))}


def rate(value: float) -> Dict[str, Any]:
    """The rating payload for one respiration value (as parse_respiration gives it).

    The value is rounded half-up to one decimal first and the band is found
    from that rounded value, so the number shown and its band always agree.
    A value falls in the first band whose max it does not exceed: 10.5 is
    above Very Low's 10, so it is Low. A missing or broken config never fails
    the assessment: the payload comes back "unrated" and the problem is logged.
    ValueError only for a value parse_respiration would refuse (or None).
    """
    number = parse_respiration(value)
    if number is None:
        raise ValueError(_RANGE_ERROR)
    unrated = {"status": "unrated", "value": number, "units": UNITS,
               "message": UNRATED_MESSAGE}
    try:
        config = load_config()
    except ValueError as exc:  # the message names the file and what is wrong
        log.error("Soil respiration could not be rated: %s", exc)
        return unrated
    except Exception:  # noqa: BLE001 - a bad config costs the rating, never the submission
        log.exception("Soil respiration could not be rated")
        return unrated

    # ROUND_HALF_UP on the decimal digits: 45.25 shows as 45.3, where float
    # round(45.25, 1) gives 45.2.
    shown = float(Decimal(str(number)).quantize(Decimal("0.1"),
                                                rounding=ROUND_HALF_UP)) + 0.0
    bands = config["bands"]
    # .get: _validate lets the last band leave "max" out (read as null).
    index = next(i for i, band in enumerate(bands)
                 if band.get("max") is None or shown <= band["max"])
    band = bands[index]
    placeholder = config.get("placeholder") is True
    prefix = PLACEHOLDER_PREFIX if placeholder else ""
    caveats = config.get("caveats")
    if not isinstance(caveats, list):
        caveats = []
    return {
        "status": "rated",
        "value": shown,
        "units": UNITS,
        "metric": _text(config.get("metric")) or METRIC,
        "band": prefix + _text(band["label"]),
        "band_index": index,
        "band_count": len(bands),
        "band_min": _bound(band["min"]),
        "band_max": _bound(band.get("max")),
        "range_text": range_text(bands, index),
        "meaning": _text(band.get("meaning")),
        "placeholder": placeholder,
        "confidence": _text(config.get("confidence")),
        "basis": _text(config.get("basis")),
        "source": _source(config["source"]),
        "caveats": [_text(item) for item in caveats if _text(item) is not None],
        # For drawing the scale.
        "bands": [{"label": prefix + _text(item["label"]), "min": _bound(item["min"]),
                   "max": _bound(item.get("max"))} for item in bands],
    }
