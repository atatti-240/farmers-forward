"""The public, no-login Farmers Forward backend (PUBLIC_ONLY=1).

A separate Flask app that serves `/api/public/*` (backend.public_api) and
nothing else. It never imports backend.app or backend.auth, so on this
deployment the admin, auth, OTP, history, submit, results, report and contact
routes do not exist (404), the admin/otp/farms tables are never created, and no
admin is seeded. The only table it creates is public_submissions, so even
sharing a database with the admin app it cannot reach admin data through any
route.

Started by backend.wsgi:app when PUBLIC_ONLY is set. Locally:

    PUBLIC_ONLY=1 /usr/bin/python3 -m backend.public_app    # port 5050
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from flask import Flask, jsonify  # noqa: E402
from flask_cors import CORS  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

from backend import config  # noqa: E402
from backend import database  # noqa: E402
from backend import public_api  # noqa: E402

# The public site's own origins, comma separated in ALLOWED_ORIGINS. The admin
# app's built-in defaults deliberately do NOT apply here: unset means local dev
# only, so a forgotten variable can never open this API to the admin origins.
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173",
               "http://localhost:4173", "http://127.0.0.1:4173")


def allowed_origins() -> list:
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or list(DEV_ORIGINS)


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    # No sessions are used; a key is still set so nothing falls back to Flask's
    # unsigned default if an extension ever reaches for one.
    app.secret_key = config.SECRET_KEY
    app.config.update(JSON_SORT_KEYS=False, MAX_CONTENT_LENGTH=8 * 1024 * 1024)

    origins = allowed_origins()
    # No credentials: the public site never sends or receives cookies.
    # The PDF's filename and a 429's wait time are read by the page, so those
    # headers are exposed to it.
    CORS(app, resources={r"/api/public/*": {"origins": origins}},
         supports_credentials=False, max_age=600,
         expose_headers=["Content-Disposition", "Retry-After"])
    print("Public API: PUBLIC_ONLY mode, CORS for %s" % ", ".join(origins), flush=True)
    print("Database: %s" % database.describe_backend(), flush=True)

    public_api.ensure_storage()
    app.register_blueprint(public_api.bp)

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        messages = {
            404: "Not found.",
            405: "That method isn't allowed here.",
            413: "That upload is too large.",
        }
        return jsonify({"error": messages.get(exc.code, exc.description or exc.name)}), exc.code

    @app.errorhandler(Exception)
    def _server_error(exc):  # pragma: no cover - last resort
        app.logger.exception("unhandled error")
        return jsonify({"error": "Something went wrong on our side. Please try again."}), 500

    return app


if __name__ == "__main__":
    create_app().run(port=int(os.environ.get("PORT", "5050")), debug=True)
