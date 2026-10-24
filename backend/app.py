"""Farmers Forward - Flask backend.

All routes live under `/api`. Authentication is a three-stage session state
machine: a shared secret code (`secret_ok`) gates the login form, valid
credentials move the session to `pending_admin_id`, and a correct OTP promotes
it to `authed`. Field submissions are analyzed by the soil model, persisted to
SQLite, and rendered to branded PDFs on demand.

Importable as `wsgi:app` (module-level `app`). Runs on port 5000 in dev.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import sys
from typing import Dict, List, Tuple

# The app uses absolute package imports (`from backend import ...`), which require
# the repo root (the parent of `backend/`) to be on sys.path. Guarantee it here so
# the worker boots under any gunicorn launcher / working directory (see wsgi.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from flask import Flask, Response, abort, jsonify, request, session  # noqa: E402
from flask_cors import CORS  # noqa: E402

from backend import auth  # noqa: E402
from backend import config  # noqa: E402
from backend import database  # noqa: E402
from backend import model_runner  # noqa: E402
from backend import pdf_report  # noqa: E402
from backend import soil_info  # noqa: E402
from backend import microbial  # noqa: E402

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
# Fail closed: a PUBLIC_ONLY process must never build the admin app, which would
# create the admin tables, seed an admin and register every admin route. The
# public deployment starts backend.wsgi:app, which picks backend.public_app.
if config.PUBLIC_ONLY:
    raise RuntimeError("PUBLIC_ONLY is set: start backend.wsgi:app (or "
                       "backend.public_app), not the admin app")

# Refuse to start in production with insecure defaults; warn loudly in dev.
_insecure = config.assert_secure()
if _insecure:
    print("!" * 64, flush=True)
    print("  WARNING: running with insecure defaults (dev only):", flush=True)
    for _p in _insecure:
        print("    - %s" % _p, flush=True)
    print("  Set APP_ENV=production and strong values in .env before deploying.",
          flush=True)
    print("!" * 64, flush=True)

# A fixed bcrypt hash of a random password, compared against on the
# username-not-found path so login timing does not reveal whether a username
# exists (mitigates account enumeration).
_DUMMY_HASH = auth.hash_password(secrets.token_urlsafe(16))

# Built React SPA lives here (frontend/dist). We serve it ourselves via the
# serve_frontend catch-all below, so static_folder is disabled to avoid Flask's
# automatic /<path> static route shadowing the SPA fallback.
DIST_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))

app = Flask(__name__, static_folder=None)
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=True,
    JSON_SORT_KEYS=False,
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

CORS(app, supports_credentials=True, origins=config.ALLOWED_ORIGINS)
# Print the allow-list on every boot. A rejected browser request shows up as a
# missing Access-Control-Allow-Origin with a 200 status, which looks like a
# frontend bug, so the effective list belongs in the deploy log next to the
# other startup diagnostics.
print("CORS: %d allowed origin(s) from %s: %s"
      % (len(config.ALLOWED_ORIGINS), config.env_source("ALLOWED_ORIGINS"),
         ", ".join(config.ALLOWED_ORIGINS)), flush=True)

# Initialize the database and seed the default admin at import/startup so both
# `flask run`/gunicorn and `python app.py` are ready to serve immediately.
# Whether the DB file already existed is captured before init_db() creates it --
# on an ephemeral disk (Render's default) a "NO" here explains a vanished admin.
# On Postgres the data is off-box and survives deploys, so the "did the file
# exist" question is only meaningful for the SQLite fallback.
_db_existed_at_boot = (None if database.using_postgres()
                       else os.path.exists(config.DATABASE_PATH))
print("Database: %s" % database.describe_backend(), flush=True)
database.init_db()
# Report where the admin credentials come from on EVERY boot, before seeding.
# seed_default_admin() is a no-op once the table has rows, so this is the only
# place that says out loud whether DEFAULT_ADMIN_PASSWORD was picked up from the
# environment or a throwaway random password is in play.
auth.log_admin_credential_status(db_existed_at_boot=_db_existed_at_boot)
auth.seed_default_admin()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MEASUREMENT_KEYS = ("ph", "ec", "nitrogen", "phosphorus", "potassium")
# Auto grids cap at 12 x 12; leave room for manual points on top.
MAX_SCORE_SECTIONS = 500


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _slug(text: str, fallback: str = "field") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-").lower()
    return slug or fallback


def _pdf_response(pdf_bytes: bytes, filename: str) -> Response:
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="%s"' % filename,
            "Content-Length": str(len(pdf_bytes)),
        },
    )


def _clear_pending(keep_secret: bool = False) -> None:
    keys = ["pending_admin_id", "otp_locked"]
    if not keep_secret:
        keys.append("secret_ok")
    for key in keys:
        session.pop(key, None)


def _clean_crop(sec: Dict, label: str) -> Tuple[str, str]:
    """(crop_type, error) for one section: one trimmed crop name, no control
    characters, at most 100 characters."""
    crop_type = sec.get("crop_type")
    if not isinstance(crop_type, str) or not crop_type.strip():
        return "", "section '%s' requires one crop_type" % label
    crop_type = crop_type.strip()
    if len(crop_type) > 100 or any(ord(char) < 32 or ord(char) == 127 for char in crop_type):
        return "", "section '%s' crop_type must be at most 100 characters without control characters" % label
    return crop_type, ""


def _validate_sections(payload, require_crop: bool = True,
                       max_sections: int = 0) -> Tuple[List[Dict], str]:
    """Validate + coerce the submit payload's sections.

    With `require_crop=False` (live scoring) only the label and the five
    measurements are required; coordinates are ignored and a crop_type, if
    sent, is validated and kept so the score can be crop-specific.
    Returns (clean_sections, error). On success error is ''.
    """
    if not isinstance(payload, list) or len(payload) == 0:
        return [], "sections must be a non-empty list"
    if max_sections and len(payload) > max_sections:
        return [], "at most %d sections per request" % max_sections

    clean: List[Dict] = []
    seen_labels = set()
    for i, sec in enumerate(payload):
        if not isinstance(sec, dict):
            return [], "section %d is not an object" % (i + 1)
        label = str(sec.get("section_label", "")).strip()
        if not label:
            return [], "section %d is missing section_label" % (i + 1)
        if label in seen_labels:
            return [], "duplicate section_label '%s'" % label
        seen_labels.add(label)

        if not require_crop:
            row = {"section_label": label}
            for key in MEASUREMENT_KEYS:
                try:
                    row[key] = float(sec[key])
                except (KeyError, TypeError, ValueError):
                    return [], "section '%s' is missing a numeric '%s'" % (label, key)
                if not math.isfinite(row[key]):
                    return [], "section '%s' has a non-finite '%s'" % (label, key)
            crop_type = sec.get("crop_type")
            if crop_type is not None and not (isinstance(crop_type, str) and not crop_type.strip()):
                row["crop_type"], err = _clean_crop(sec, label)
                if err:
                    return [], err
            clean.append(row)
            continue

        try:
            lat = float(sec["latitude"])
            lng = float(sec["longitude"])
        except (KeyError, TypeError, ValueError):
            return [], "section '%s' has invalid latitude/longitude" % label

        crop_type, err = _clean_crop(sec, label)
        if err:
            return [], err

        row = {"section_label": label, "latitude": lat, "longitude": lng,
               "crop_type": crop_type}
        for key in MEASUREMENT_KEYS:
            try:
                row[key] = float(sec[key])
            except (KeyError, TypeError, ValueError):
                return [], "section '%s' is missing a numeric '%s'" % (label, key)
        clean.append(row)

    return clean, ""


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------
# Every route reads its body as a JSON object (`request.get_json(...) or {}`,
# then `.get(...)`), so a body that is valid JSON but not an object -- a list, a
# string, a number -- reached that `.get` and came back as a 500, on the public
# /api/contact too. Checking once here, before any route (and before any login
# check), covers every /api endpoint, including ones added later, without
# touching each handler. A request with no body at all is left alone: the
# routes treat that as {} (logout, the report download's optional map image).
JSON_OBJECT_REQUIRED = "Please send the request body as a JSON object."


@app.before_request
def require_json_object_body():
    if request.method not in ("POST", "PUT", "PATCH") or not request.path.startswith("/api/"):
        return None
    if not request.get_data(cache=True).strip():
        return None
    data = request.get_json(silent=True) if request.is_json else None
    if not isinstance(data, dict):
        return jsonify({"error": JSON_OBJECT_REQUIRED}), 400
    return None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
@app.post("/api/auth/verify-code")
def verify_code():
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip()
    # Constant-time compare so the secret code can't be recovered character by
    # character via response-timing analysis.
    if code and secrets.compare_digest(code, config.ADMIN_SECRET_CODE):
        session["secret_ok"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False})


@app.post("/api/auth/login")
def login():
    if session.get("secret_ok") is not True:
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    admin = database.query_one(
        "SELECT * FROM admins WHERE username = ?", (username,))
    # Always run a bcrypt comparison (against a dummy hash when the username is
    # unknown) so response timing does not reveal whether the account exists.
    ok = auth.check_password(password, admin["password_hash"] if admin else _DUMMY_HASH)
    if admin is None or not ok:
        return jsonify({"error": "Invalid credentials"}), 401

    channels = auth.create_and_send_otp(admin)
    session["pending_admin_id"] = admin["id"]
    session["otp_locked"] = False
    return jsonify({"ok": True, "channels": channels})


@app.post("/api/auth/otp")
def otp():
    admin_id = session.get("pending_admin_id")
    if not admin_id:
        return jsonify({"error": "no pending login"}), 401

    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip()

    result = auth.verify_otp(admin_id, code)
    if result == "ok":
        admin = database.query_one(
            "SELECT username FROM admins WHERE id = ?", (admin_id,))
        _clear_pending()
        session["authed"] = True
        session["admin_id"] = admin_id
        if admin is not None:
            session["username"] = admin["username"]
        return jsonify({"ok": True})

    if result == "invalid":
        return jsonify({
            "error": "invalid",
            "attempts_remaining": auth.attempts_remaining(admin_id),
        }), 401

    if result == "expired":
        return jsonify({"error": "expired"}), 401

    # locked: drop the pending login but KEEP secret_ok so the admin can log in
    # again (a fresh password entry mints a new OTP) without re-entering the
    # hidden secret code.
    _clear_pending(keep_secret=True)
    return jsonify({"error": "locked"}), 423


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/auth/me")
def me():
    if session.get("authed") is True and session.get("admin_id"):
        username = session.get("username")
        if not username:
            admin = database.query_one(
                "SELECT username FROM admins WHERE id = ?",
                (session.get("admin_id"),))
            username = admin["username"] if admin is not None else None
        return jsonify({"authed": True, "username": username})
    return jsonify({"authed": False})


# ---------------------------------------------------------------------------
# Field submission + retrieval
# ---------------------------------------------------------------------------
@app.post("/api/submit")
@auth.login_required
def submit():
    data = request.get_json(silent=True) or {}
    farm_name = str(data.get("farm_name", "")).strip()
    date = str(data.get("date", "")).strip()

    if not farm_name:
        return jsonify({"error": "farm_name is required"}), 400
    if not date:
        return jsonify({"error": "date is required"}), 400

    sections, err = _validate_sections(data.get("sections"))
    if err:
        return jsonify({"error": err}), 400

    # Optional point for the mapped soil-survey profile (display only, never
    # scored). A bad point is dropped, not a 400. The profile comes from the
    # cache only, and is read before the write transaction below opens: the
    # cache's lazy CREATE TABLE would otherwise wait on this request's own lock.
    soil_point = None
    soil_profile = None
    raw_point = data.get("soil_point")
    if isinstance(raw_point, dict):
        try:
            lat, lon = soil_info.parse_coords(raw_point.get("lat"), raw_point.get("lon"))
        except (TypeError, ValueError):
            pass
        else:
            # The lookup is made at the rounded point, so store that one.
            lat_text, lon_text = soil_info.point_key(lat, lon).split(",")
            soil_point = {"lat": float(lat_text), "lon": float(lon_text)}
            soil_profile = soil_info.cached_soil_info(lat, lon)

    # Optional soil respiration lab value, one for the whole field (display
    # only, never scored). Unlike the point, a value that is not a usable
    # number is a 400; blank or missing just means none was entered.
    try:
        soil_respiration = microbial.parse_respiration(data.get("soil_respiration"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    conn = database.get_db()
    try:
        now = database.now_iso()
        # Insert the farm first (placeholder result_json) to obtain its id, which
        # the analysis embeds into sample_ids and its own farm_id field.
        cur = conn.execute(
            "INSERT INTO farms (farm_name, date, n_sections, mean_health_score, "
            "result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (farm_name, date, len(sections), None, "{}", now))
        farm_id = cur.lastrowid

        analysis = model_runner.run_analysis(farm_name, date, sections, farm_id)
        if soil_point is not None:
            analysis["soil_point"] = soil_point
            analysis["soil_profile"] = soil_profile
        if soil_respiration is not None:
            analysis["microbial"] = microbial.rate(soil_respiration)
        mean_score = analysis["field_summary"]["mean_health_score"]

        conn.execute(
            "UPDATE farms SET result_json = ?, mean_health_score = ? WHERE id = ?",
            (json.dumps(analysis), mean_score, farm_id))

        # Persist each submitted sample.
        for sec in sections:
            conn.execute(
                "INSERT INTO samples (farm_id, sample_id, section_label, latitude, "
                "longitude, ph, ec, nitrogen, phosphorus, potassium, crop_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (farm_id, "%s-%s" % (farm_id, sec["section_label"]),
                 sec["section_label"], sec["latitude"], sec["longitude"],
                 sec["ph"], sec["ec"], sec["nitrogen"], sec["phosphorus"],
                 sec["potassium"], sec["crop_type"]))

        # Persist each per-section result.
        for res in analysis["results"]:
            viable = res.get("crop_suitability", {}).get("viable_now", [])
            conn.execute(
                "INSERT INTO results (farm_id, section_label, health_score, "
                "health_category, viable_crops, recommendations) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (farm_id, res["section_label"], res["health_score"],
                 res["health_category"], json.dumps(viable),
                 json.dumps(res.get("recommendations", []))))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return jsonify(analysis)


@app.post("/api/score")
@auth.login_required
def score():
    """Live health scores for the data-entry map (no storage; crop optional)."""
    data = request.get_json(silent=True) or {}
    sections, err = _validate_sections(data.get("sections"), require_crop=False,
                                       max_sections=MAX_SCORE_SECTIONS)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"scores": model_runner.score_sections(sections)})


@app.get("/api/results/<int:farm_id>")
@auth.login_required
def get_results(farm_id: int):
    row = database.query_one(
        "SELECT result_json FROM farms WHERE id = ?", (farm_id,))
    if row is None:
        return jsonify({"error": "not found"}), 404
    return Response(row["result_json"], mimetype="application/json")


@app.get("/api/history")
@auth.login_required
def history():
    rows = database.query_all(
        "SELECT id, farm_name, date, created_at, n_sections, mean_health_score "
        # created_at is an ISO8601 UTC string, so lexicographic order is
        # chronological order. The SQLite-only date function this used to wrap
        # around created_at does not exist in Postgres and made /api/history 500.
        "FROM farms ORDER BY created_at DESC, id DESC")
    farms = [{
        "farm_id": r["id"],
        "farm_name": r["farm_name"],
        "date": r["date"],
        "created_at": r["created_at"],
        "n_sections": r["n_sections"],
        "mean_health_score": r["mean_health_score"],
    } for r in rows]
    return jsonify({"farms": farms})


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------
@app.post("/api/contact")
def contact():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip()
    message = str(data.get("message", "")).strip()

    if not name or not email or not message:
        return jsonify({"error": "name, email and message are all required"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address"}), 400

    database.execute(
        "INSERT INTO contacts (name, email, message, created_at) "
        "VALUES (?, ?, ?, ?)",
        (name, email, message, database.now_iso()))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Soil profile
# ---------------------------------------------------------------------------
@app.get("/api/soil-info")
@auth.login_required
def get_soil_info():
    """Mapped USDA soil-survey data for one point (display only, never scored).

    Only a bad coordinate is a 400. Every lookup outcome, including the survey
    services not answering, is a 200 whose `status` says what happened.
    """
    try:
        payload = soil_info.get_soil_info(request.args.get("lat"),
                                          request.args.get("lon"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload)


# ---------------------------------------------------------------------------
# PDF reports
# ---------------------------------------------------------------------------
def _load_analysis(farm_id: int):
    row = database.query_one(
        "SELECT result_json FROM farms WHERE id = ?", (farm_id,))
    if row is None:
        return None
    try:
        return json.loads(row["result_json"])
    except (ValueError, TypeError):
        return None


@app.get("/api/report/<int:farm_id>/section/<path:section_label>")
@auth.login_required
def report_section(farm_id: int, section_label: str):
    analysis = _load_analysis(farm_id)
    if not analysis:
        return jsonify({"error": "not found"}), 404

    section = next((r for r in analysis.get("results", [])
                    if r.get("section_label") == section_label), None)
    if section is None:
        return jsonify({"error": "not found"}), 404

    pdf_bytes = pdf_report.build_section_pdf(
        section, analysis.get("farm_name", ""), analysis.get("date", ""))
    filename = "%s_%s.pdf" % (_slug(analysis.get("farm_name")),
                              _slug(section_label, "section"))
    return _pdf_response(pdf_bytes, filename)


@app.route("/api/report/<int:farm_id>/all", methods=["GET", "POST"])
@auth.login_required
def report_all(farm_id: int):
    """The combined report.

    POST additionally accepts `{"map_image": "data:image/jpeg;base64,..."}`: the
    results map as the browser rasterised it, with its score colours, which is
    embedded as the report's field map page. Rendering that map server-side
    would need a headless browser, which the free tier cannot run, so the
    client sends the picture. GET still works and simply omits the map.
    """
    analysis = _load_analysis(farm_id)
    if not analysis:
        return jsonify({"error": "not found"}), 404

    map_image = None
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        candidate = data.get("map_image")
        # A malformed image is dropped here rather than failing the download;
        # build_combined_pdf validates the payload itself before decoding.
        if isinstance(candidate, str) and candidate.startswith("data:image/"):
            map_image = candidate

    # A soil profile that had not loaded (or only partly) at submit time may be
    # cached by now, since the results page fetches it. Cache only: a PDF
    # download never waits on the soil survey services.
    soil_point = analysis.get("soil_point")
    soil_profile = analysis.get("soil_profile")
    if isinstance(soil_point, dict) and (
            not isinstance(soil_profile, dict)
            or soil_profile.get("status") in ("partial", "unavailable")):
        cached = soil_info.cached_soil_info(soil_point.get("lat"), soil_point.get("lon"))
        if cached:
            analysis["soil_profile"] = cached

    pdf_bytes = pdf_report.build_combined_pdf(analysis, map_image=map_image)
    filename = "%s_full_report.pdf" % _slug(analysis.get("farm_name"))
    return _pdf_response(pdf_bytes, filename)


# ---------------------------------------------------------------------------
# React SPA (must be LAST: it is the catch-all fallback for client-side routes)
# ---------------------------------------------------------------------------
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    from flask import send_from_directory
    # Never let the SPA fallback swallow unknown API routes -- return a real 404.
    if path == "api" or path.startswith("api/"):
        abort(404)
    candidate = os.path.join(DIST_DIR, path)
    if path and os.path.isfile(candidate):
        return send_from_directory(DIST_DIR, path)
    return send_from_directory(DIST_DIR, "index.html")


# ---------------------------------------------------------------------------
# Dev entrypoint (production uses gunicorn via wsgi.py; this block never runs
# under gunicorn, so no route may be defined below it).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(port=5000, debug=True)

