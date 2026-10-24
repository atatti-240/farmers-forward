# Farmers Forward: public site

The no-login public version of Farmers Forward, built for the Congressional App
Challenge. Anyone can map a field, enter soil readings, see a health score and
plain-English plan for every region, and download a PDF.

This lives only on the `public-site` branch and is **never merged into `main`**.
It runs as two Render services of its own and never touches the admin app,
its database tables, or its Render services.

| Piece | Code | What it is |
| --- | --- | --- |
| Public backend | `backend/public_app.py`, `backend/public_api.py` | Flask app serving only `/api/public/*`, started with `PUBLIC_ONLY=1` |
| Public frontend | `public-site/` (this folder) | Vite + React static site: landing page `/` and the tool `/tool` |

## How it stays isolated

- **`PUBLIC_ONLY=1`**: `backend/wsgi.py` serves `backend.public_app` and never
  imports `backend.app` or `backend.auth`. Every admin, auth, OTP, history,
  submit, results, report and contact route returns 404. No admin/otp/farms
  tables are created, and no admin is seeded. `backend/app.py` refuses to boot
  with the flag set.
- **Storage**: one table, `public_submissions` (uuid id, created_at, crop_type,
  inputs jsonb, results jsonb, pdf_token_hash, pdf_token_expires_at). It's
  created on boot (idempotent) with Row Level Security on and no policies, so
  Supabase's anon REST API can read nothing. No raw IP addresses are stored.
- **Nothing can be read back**. `POST /api/public/assessments` returns the
  results directly. Its `pdf_token` (only the SHA-256 is stored) unlocks
  `POST /api/public/assessments/report` for one hour, and does nothing else.
- **Same model**: the endpoints call `model_runner.score_sections`,
  `model_runner.run_analysis` and `pdf_report.build_combined_pdf`, exactly
  as the admin app does.

## API

| Endpoint | Limit per IP |
| --- | --- |
| `GET /api/public/health`: wake-up ping, warms the model in the background | 30/min |
| `POST /api/public/score`: live map colours, nothing stored | 60/min |
| `POST /api/public/assessments`: analyze + store + return results and PDF token | 10 per 10 min, 40/day |
| `POST /api/public/assessments/report`: `{token, map_image?}` → PDF | 5 per 10 min, 2 rendering at once |
| `GET /api/public/soil-info?lat&lon`: 404 until the soil-data feature is merged in | 20/min |

Every POST body must be a JSON object (anything else is a friendly 400).
Readings are range-checked: pH 0–14, EC 0–30 dS/m, N and P 0–2,000 ppm,
K 0–10,000 ppm. At most 150 points and 100-character names.

Fields that appear once `main` (with the fert-calculator and soil-data
features) is merged into this branch, all rendered only when present:
`results[].fertilizer_plan`, `fertilizer_total`, `soil_point`, `soil_profile`.

## Run locally

```bash
# backend (port 5050; uses a local SQLite file unless DATABASE_URL is set)
PUBLIC_ONLY=1 /usr/bin/python3 -m backend.public_app

# frontend (port 5173; /api is proxied to :5050)
cd public-site && npm ci && npm run dev
```

Tests: `cd backend/tests && /usr/bin/python3 -m unittest discover -p 'test_*.py'`
(`test_public_api.py` covers the public API and PUBLIC_ONLY mode).

## Deploy on Render (two NEW services)

### 1. Public backend: Web Service

| Setting | Value |
| --- | --- |
| Repository / Branch | `atatti-240/farmers-forward` / `public-site` |
| Root Directory | *(blank: repo root)* |
| Runtime | Python 3 |
| Build Command | `pip install -r backend/requirements.txt` |
| Start Command | `gunicorn backend.wsgi:app --workers 1 --threads 4 --timeout 120` |
| Instance type | Free |
| Health Check Path | `/api/public/health` (optional) |

Environment variables:

| Key | Value |
| --- | --- |
| `PUBLIC_ONLY` | `1` |
| `SECRET_KEY` | a NEW random value: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `ALLOWED_ORIGINS` | the public static site's URL only, e.g. `https://farmers-forward-public.onrender.com` |
| `DATABASE_URL` | Supabase pooler URI (port 6543) |
| `APP_ENV` | `production` |
| `COOKIE_SECURE` | `true` |
| `PYTHON_VERSION` | the same value as the existing backend service, if it sets one |

One worker on purpose: the rate limits live in that process's memory, and one
copy of the model fits the free instance's 512 MB.

### 2. Public frontend: Static Site

| Setting | Value |
| --- | --- |
| Repository / Branch | `atatti-240/farmers-forward` / `public-site` |
| Root Directory | `public-site` |
| Build Command | `npm ci && npm run build` |
| Publish Directory | `dist` |
| Redirects/Rewrites | Source `/*` → Destination `/index.html`, Action **Rewrite** |

Environment variables:

| Key | Value |
| --- | --- |
| `VITE_API_URL` | `https://<public-backend>.onrender.com/api/public` |
| `NODE_VERSION` | `22` |

After the static site has its URL, set it as the backend's `ALLOWED_ORIGINS`
and redeploy the backend.
