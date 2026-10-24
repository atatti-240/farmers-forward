"""WSGI entrypoint for gunicorn.

Works under any of these start commands, from either the repo root or the
`backend/` directory:

    gunicorn wsgi:app            # rootDir = backend/
    gunicorn backend.wsgi:app    # rootDir = repo root
    gunicorn backend.app:app     # rootDir = repo root (skips this shim)

The app uses absolute package imports (`from backend import ...`), which require
the *repo root* (the parent of `backend/`) to be on `sys.path`. We guarantee that
here before importing the app, so the worker boots regardless of the launcher's
working directory.

With PUBLIC_ONLY=1 the same entrypoint serves the public, no-login app
(backend.public_app) instead.
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend import config  # noqa: E402  (import after sys.path is set up)

# PUBLIC_ONLY (the public-site deployment) serves only /api/public/*; the admin
# app module is never imported, so none of its routes or boot steps exist.
if config.PUBLIC_ONLY:
    from backend.public_app import create_app  # noqa: E402
    app = create_app()
else:
    from backend.app import app  # noqa: E402

__all__ = ["app"]
