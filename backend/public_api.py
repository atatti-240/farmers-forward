"""Public, no-login assessment API under `/api/public` (public-site only).

Anyone can score a field and download its PDF here, with the SAME model code the
admin app uses (model_runner.score_sections / run_analysis and
pdf_report.build_combined_pdf). Only the storage is separate:

  * Submissions go to their own `public_submissions` table, created on boot by
    ensure_storage(). Nothing here reads or writes farms / samples / results,
    and no existing table is altered.
  * Nothing stored can be read back. POST /assessments returns the full
    analysis directly, plus a short-lived PDF token so the same person can
    download their report straight away. The token is the only key, only its
    SHA-256 is stored, and it expires after PDF_TOKEN_TTL_SECONDS.
  * No raw IP address is stored anywhere. Rate limiting is in memory, keyed by
    a salted hash of the client address that dies with the process.

Every POST body must be a JSON object; anything else is a friendly 400 rather
than the 500 a missing `.get` would cause.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.util
import json
import math
import re
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, request

from backend import database
from backend import model_runner
from backend import pdf_report

bp = Blueprint("public_api", __name__, url_prefix="/api/public")

TABLE = "public_submissions"

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_SECTIONS = 150
MAX_TEXT = 100
MAX_LABEL = 40
MAX_POLYGON_VERTICES = 1000
MAX_GRID = 30
# A map image is a JPEG data URL; mapCapture.js keeps it under 5 MB.
MAX_MAP_IMAGE_CHARS = 6 * 1024 * 1024
PDF_TOKEN_TTL_SECONDS = 3600
# Concurrent PDF renders. reportlab plus a decoded map image is the heaviest
# thing this process does, and the free instance has 512 MB.
PDF_CONCURRENCY = 2
PDF_WAIT_SECONDS = 20

# Inclusive sane ranges for what a soil sensor or lab can report. Values outside
# these are typos (a pH of 65, say), not soil.
MEASUREMENT_RANGES = {
    "ph": (0.0, 14.0, "pH", ""),
    "ec": (0.0, 30.0, "EC", " dS/m"),
    "nitrogen": (0.0, 2000.0, "Nitrogen", " ppm"),
    "phosphorus": (0.0, 2000.0, "Phosphorus", " ppm"),
    "potassium": (0.0, 10000.0, "Potassium", " ppm"),
}
MEASUREMENT_KEYS = tuple(MEASUREMENT_RANGES)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class BadInput(ValueError):
    """A validation failure whose message is safe to show the user."""


# ---------------------------------------------------------------------------
# Rate limiting (in memory, per process)
# ---------------------------------------------------------------------------
# The public backend runs ONE gunicorn worker (see README), so one process sees
# every request and these counters are the whole picture.
_IP_SALT = secrets.token_bytes(16)


def _client_key() -> str:
    """A salted hash of the caller's address. The raw address is never kept.

    Render sits behind Cloudflare, which sets CF-Connecting-IP / True-Client-IP
    itself (a client cannot forge them through it). X-Forwarded-For's first hop
    is the fallback; forging that only dodges the per-IP limit, and the global
    limits below still apply.
    """
    ip = (request.headers.get("CF-Connecting-IP")
          or request.headers.get("True-Client-IP")
          or (request.headers.get("X-Forwarded-For") or "").split(",")[0]
          or request.remote_addr
          or "unknown").strip()
    return hmac.new(_IP_SALT, ip.encode("utf-8", "replace"), hashlib.sha256).hexdigest()


class RateLimiter:
    """Sliding-window counters: `check(key, [(count, seconds), ...])`."""

    def __init__(self):
        self._hits: Dict[str, deque] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def check(self, key: str, limits: List[Tuple[int, int]]) -> Optional[int]:
        """Record a hit and return None, or return seconds to wait (no hit)."""
        now = time.monotonic()
        longest = max(window for _, window in limits)
        with self._lock:
            self._sweep(now)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > longest:
                hits.popleft()
            for count, window in limits:
                recent = [t for t in hits if now - t <= window]
                if len(recent) >= count:
                    return max(1, int(math.ceil(window - (now - recent[0]))))
            hits.append(now)
            return None

    def _sweep(self, now: float) -> None:
        # Drop idle keys every few minutes so memory stays flat. A key idle for
        # a day has no hits inside any window.
        if now - self._last_sweep < 300:
            return
        self._last_sweep = now
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] > 86400]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()

# Per-IP limits, and a whole-site ceiling for the expensive endpoints so a
# client rotating forwarded addresses still cannot run the instance flat out.
LIMITS = {
    "health": [(30, 60)],
    "score": [(60, 60)],
    "assessments": [(10, 600), (40, 86400)],
    "report": [(5, 600)],
    "soil": [(20, 60)],
}
GLOBAL_LIMITS = {
    "assessments": [(300, 3600)],
    "report": [(120, 3600)],
    "soil": [(300, 3600)],
}


def _too_many(retry_after: int):
    resp = jsonify({
        "error": "Too many requests. Please wait a moment and try again.",
        "retry_after": retry_after,
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(retry_after)
    return resp


def rate_limited(name: str):
    """Apply LIMITS[name] per client (and GLOBAL_LIMITS[name] site-wide)."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            wait = limiter.check("%s:%s" % (name, _client_key()), LIMITS[name])
            if wait is None and name in GLOBAL_LIMITS:
                wait = limiter.check("%s:*" % name, GLOBAL_LIMITS[name])
            if wait is not None:
                return _too_many(wait)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def json_object(view):
    """Pass the request body to the view as a dict, or answer 400."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Please send the form data as a JSON object."}), 400
        return view(data, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _text(value: Any, what: str, limit: int = MAX_TEXT, required: bool = True) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise BadInput("%s is required." % what)
        return ""
    if not isinstance(value, str):
        raise BadInput("%s must be text." % what)
    value = " ".join(value.split())
    if len(value) > limit:
        raise BadInput("%s must be at most %d characters." % (what, limit))
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise BadInput("%s contains characters that aren't allowed." % what)
    return value


def _number(value: Any, what: str, lo: float, hi: float, unit: str = "") -> float:
    # bool is an int subclass; True is not a pH.
    if isinstance(value, bool) or value is None:
        raise BadInput("%s needs a number." % what)
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise BadInput("%s needs a number." % what) from None
    if not math.isfinite(num) or num < lo or num > hi:
        raise BadInput("%s must be between %g and %g%s." % (what, lo, hi, unit))
    return num


def _coords(lat: Any, lng: Any, what: str) -> Tuple[float, float]:
    return (_number(lat, "%s latitude" % what, -90.0, 90.0),
            _number(lng, "%s longitude" % what, -180.0, 180.0))


def clean_sections(payload: Any, full: bool) -> List[Dict]:
    """Validate the regions of a request.

    `full` (an assessment) also needs coordinates and a crop per region; live
    scoring needs only the label and the five readings, with an optional crop.
    """
    if not isinstance(payload, list) or not payload:
        raise BadInput("Add at least one sample point with soil readings.")
    if len(payload) > MAX_SECTIONS:
        raise BadInput("At most %d sample points per field." % MAX_SECTIONS)

    clean: List[Dict] = []
    seen = set()
    for i, sec in enumerate(payload):
        if not isinstance(sec, dict):
            raise BadInput("Sample point %d is not valid." % (i + 1))
        label = _text(sec.get("section_label"), "Sample point %d's label" % (i + 1), MAX_LABEL)
        if label in seen:
            raise BadInput("Two sample points are both labelled '%s'." % label)
        seen.add(label)
        where = "Point %s" % label

        row: Dict[str, Any] = {"section_label": label}
        for key, (lo, hi, name, unit) in MEASUREMENT_RANGES.items():
            row[key] = _number(sec.get(key), "%s: %s" % (where, name), lo, hi, unit)

        crop = _text(sec.get("crop_type"), "%s: crop" % where, required=full)
        if crop:
            row["crop_type"] = crop
        if full:
            row["latitude"], row["longitude"] = _coords(
                sec.get("latitude"), sec.get("longitude"), where)
            # Acres this point stands for (the fertilizer plan's totals use it
            # once that feature is merged in). Optional.
            if sec.get("area_acres") is not None:
                row["area_acres"] = _number(sec.get("area_acres"), "%s: area" % where,
                                            0.0, 100000.0, " acres")
        clean.append(row)
    return clean


def clean_field(field: Any) -> Dict:
    """The drawn field (boundary, grid, points), kept only as submitted context."""
    if field is None:
        return {}
    if not isinstance(field, dict):
        raise BadInput("The field outline is not valid.")
    out: Dict[str, Any] = {}

    polygon = field.get("polygon")
    if polygon is not None:
        if not isinstance(polygon, list) or len(polygon) > MAX_POLYGON_VERTICES:
            raise BadInput("The field outline is not valid.")
        ring = []
        for vertex in polygon:
            if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
                raise BadInput("The field outline is not valid.")
            ring.append(list(_coords(vertex[0], vertex[1], "Field corner")))
        out["polygon"] = ring

    grid = field.get("grid")
    if grid is not None:
        if not isinstance(grid, dict):
            raise BadInput("The grid is not valid.")
        out["grid"] = {k: int(_number(grid.get(k), "Grid %s" % k, 1, MAX_GRID))
                       for k in ("rows", "cols")}

    points = field.get("points")
    if points is not None:
        if not isinstance(points, list) or len(points) > MAX_SECTIONS:
            raise BadInput("The sample points are not valid.")
        kept = []
        for p in points:
            if not isinstance(p, dict):
                raise BadInput("The sample points are not valid.")
            label = _text(p.get("label"), "A sample point's label", MAX_LABEL)
            lat, lng = _coords(p.get("lat"), p.get("lng"), "Point %s" % label)
            kept.append({"label": label, "lat": lat, "lng": lng})
        out["points"] = kept
    return out


def _clean_date(value: Any) -> str:
    if value is None or value == "":
        return datetime.now(timezone.utc).date().isoformat()
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise BadInput("The date must look like 2026-10-23.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise BadInput("The date must look like 2026-10-23.") from None
    return value


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS public_submissions (
    id                   UUID PRIMARY KEY,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    crop_type            TEXT,
    inputs               JSONB NOT NULL,
    results              JSONB NOT NULL,
    pdf_token_hash       TEXT UNIQUE,
    pdf_token_expires_at TIMESTAMPTZ
);
"""

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS public_submissions (
    id                   TEXT PRIMARY KEY,
    created_at           TEXT NOT NULL,
    crop_type            TEXT,
    inputs               TEXT NOT NULL,
    results              TEXT NOT NULL,
    pdf_token_hash       TEXT UNIQUE,
    pdf_token_expires_at TEXT
);
"""


def ensure_storage() -> None:
    """Create public_submissions if missing. Idempotent; safe on every boot.

    On Postgres, Row Level Security is switched on with NO policies, so
    Supabase's anon/authenticated REST roles can read nothing. The backend's
    own role owns the table, and RLS does not apply to a table's owner.
    """
    conn = database.get_db()
    try:
        if database.using_postgres():
            conn.executescript(SCHEMA_POSTGRES)
            conn.execute("ALTER TABLE public_submissions ENABLE ROW LEVEL SECURITY")
        else:
            conn.executescript(SCHEMA_SQLITE)
        conn.commit()
    finally:
        conn.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_datetime(value: Any) -> Optional[datetime]:
    """A stored timestamp, timezone-aware. psycopg2 hands back a timestamp
    object; SQLite hands back the ISO string that was written."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _as_json(value: Any) -> Any:
    """A stored JSON column as Python (psycopg2 already decodes JSONB)."""
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _store(crop_type: Optional[str], inputs: Dict, results: Dict) -> Tuple[str, datetime]:
    """Insert one submission; return (pdf_token, expires_at)."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=PDF_TOKEN_TTL_SECONDS)
    database.execute(
        "INSERT INTO public_submissions (id, created_at, crop_type, inputs, results, "
        "pdf_token_hash, pdf_token_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), now.isoformat(), crop_type, json.dumps(inputs),
         json.dumps(results), _token_hash(token), expires.isoformat()))
    return token, expires


def _load_for_pdf(token: str) -> Optional[Dict]:
    """The stored analysis for a live PDF token, or None (wrong or expired)."""
    row = database.query_one(
        "SELECT results, pdf_token_expires_at FROM public_submissions "
        "WHERE pdf_token_hash = ?", (_token_hash(token),))
    if row is None:
        return None
    expires = _as_datetime(row["pdf_token_expires_at"])
    if expires is None or expires <= datetime.now(timezone.utc):
        return None
    results = _as_json(row["results"])
    return results if isinstance(results, dict) else None


# ---------------------------------------------------------------------------
# Optional features (present once their branches are merged in)
# ---------------------------------------------------------------------------
def _soil_info():
    """backend.soil_info when the soil-data feature is present, else None."""
    if importlib.util.find_spec("backend.soil_info") is None:
        return None
    return importlib.import_module("backend.soil_info")


def _attach_soil_profile(analysis: Dict, raw_point: Any) -> None:
    """Mirror the admin submit: a cached soil-survey profile for the field's
    point, display only. A bad point or a missing feature is simply skipped."""
    soil = _soil_info()
    if soil is None or not isinstance(raw_point, dict):
        return
    try:
        lat, lon = soil.parse_coords(raw_point.get("lat"), raw_point.get("lon"))
    except (TypeError, ValueError):
        return
    lat_text, lon_text = soil.point_key(lat, lon).split(",")
    analysis["soil_point"] = {"lat": float(lat_text), "lon": float(lon_text)}
    analysis["soil_profile"] = soil.cached_soil_info(lat, lon)


# ---------------------------------------------------------------------------
# Model warm-up
# ---------------------------------------------------------------------------
_warm_lock = threading.Lock()
_warm_state = {"status": "cold"}  # cold -> warming -> ready | failed

# A typical reading, scored once to pull the ML stack and model into memory.
_WARM_SECTION = {"section_label": "warm", "ph": 6.5, "ec": 0.5, "nitrogen": 20.0,
                 "phosphorus": 30.0, "potassium": 150.0}


def _warm_model() -> None:
    try:
        model_runner.score_sections([dict(_WARM_SECTION)])
        _warm_state["status"] = "ready"
    except Exception:  # pragma: no cover - logged, next /health retries
        _warm_state["status"] = "failed"
        import traceback
        traceback.print_exc()


def start_warmup() -> str:
    with _warm_lock:
        if _warm_state["status"] in ("cold", "failed"):
            _warm_state["status"] = "warming"
            threading.Thread(target=_warm_model, name="model-warmup", daemon=True).start()
        return _warm_state["status"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.get("/health")
@rate_limited("health")
def health():
    """Cheap liveness ping that also starts loading the model in the background,
    so the first assessment after a cold start doesn't pay for it."""
    return jsonify({"ok": True, "model": start_warmup()})


@bp.post("/score")
@rate_limited("score")
@json_object
def score(data):
    """Live health scores for the data-entry map. Nothing is stored."""
    try:
        sections = clean_sections(data.get("sections"), full=False)
    except BadInput as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"scores": model_runner.score_sections(sections)})


@bp.post("/assessments")
@rate_limited("assessments")
@json_object
def create_assessment(data):
    """Run the full analysis, store it in public_submissions, return it.

    The response is the only copy the caller gets: there is no endpoint that
    reads a submission back. `pdf_token` unlocks POST /assessments/report for
    this submission for one hour.
    """
    try:
        farm_name = _text(data.get("farm_name"), "Field name", required=False) or "My field"
        date = _clean_date(data.get("date"))
        sections = clean_sections(data.get("sections"), full=True)
        field = clean_field(data.get("field"))
        soil_point = data.get("soil_point")
        if soil_point is not None:
            if not isinstance(soil_point, dict):
                raise BadInput("The soil lookup point is not valid.")
            lat, lon = _coords(soil_point.get("lat"), soil_point.get("lon"), "Soil lookup point")
            soil_point = {"lat": lat, "lon": lon}
    except BadInput as exc:
        return jsonify({"error": str(exc)}), 400

    analysis = model_runner.run_analysis(farm_name, date, sections, None)
    analysis.pop("farm_id", None)
    analysis["source"] = "public"
    _attach_soil_profile(analysis, soil_point)

    crops = sorted({s["crop_type"] for s in sections})
    inputs = {"farm_name": farm_name, "date": date, "field": field,
              "sections": sections, "soil_point": soil_point}
    token, expires = _store(", ".join(crops)[:500] or None, inputs, analysis)

    return jsonify({
        "analysis": analysis,
        "pdf_token": token,
        "pdf_token_expires_at": expires.isoformat(),
    })


_pdf_slots = threading.BoundedSemaphore(PDF_CONCURRENCY)


def _slug(text: str, fallback: str = "field") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-").lower()
    return slug[:60] or fallback


@bp.post("/assessments/report")
@rate_limited("report")
@json_object
def assessment_report(data):
    """The combined PDF for the submission a live `token` belongs to.

    Accepts `map_image` (a data:image/... URL the browser rasterised from the
    results map), exactly like the admin report endpoint.
    """
    token = data.get("token")
    if not isinstance(token, str) or not (20 <= len(token) <= 100):
        return jsonify({"error": "This report link is not valid. Run the assessment again."}), 400

    map_image = data.get("map_image")
    if map_image is not None and not (
            isinstance(map_image, str) and map_image.startswith("data:image/")
            and len(map_image) <= MAX_MAP_IMAGE_CHARS):
        map_image = None  # a bad picture drops the map page, not the download

    analysis = _load_for_pdf(token)
    if analysis is None:
        return jsonify({"error": "This report has expired. Run the assessment again "
                                 "to download a fresh PDF."}), 404

    if not _pdf_slots.acquire(timeout=PDF_WAIT_SECONDS):
        return jsonify({"error": "The report printer is busy. Please try again in a moment."}), 503
    try:
        pdf_bytes = pdf_report.build_combined_pdf(analysis, map_image=map_image)
    finally:
        _pdf_slots.release()

    filename = "%s_soil_report.pdf" % _slug(analysis.get("farm_name"))
    return Response(pdf_bytes, mimetype="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "Content-Length": str(len(pdf_bytes)),
        "Cache-Control": "no-store",
    })


@bp.get("/soil-info")
@rate_limited("soil")
def soil_info():
    """Mapped USDA soil-survey data for one point, once the soil-data feature
    is merged in. Until then: 404 with status 'unavailable'."""
    soil = _soil_info()
    if soil is None:
        return jsonify({"error": "Soil profile lookups are not available yet.",
                        "status": "unavailable"}), 404
    try:
        payload = soil.get_soil_info(request.args.get("lat"), request.args.get("lon"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload)
