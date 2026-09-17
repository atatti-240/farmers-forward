# Farmers Forward

**Farmers Forward** is a soil-health platform for Fort Bend County farmers along
the Brazos River. Field staff draw a field boundary on a satellite map, drop a
grid of sample points, enter five soil measurements per point - **pH, EC,
Nitrogen (N), Phosphorus (P), and Potassium (K)** - and the app runs a trained
machine-learning pipeline to produce a health score, the dominant soil problem,
crop-viability guidance, and prioritized recommendations, all downloadable as
branded PDF reports.

- **Public site** - mission, how it works, where we serve, and a contact form.
- **Private dashboard** - a 4-step workflow (Field Setup → Data Entry → Review →
  Results), gated behind a hidden staff login. Field Setup includes a Farm Finder
  that jumps the map to an address (via OpenStreetMap Nominatim) or coordinates.
- **Brand** - Farmers Forward blue `#4AAFE0` with a red `#E8302A` accent; health
  scores render green / amber / red (`>=70` green, `40–69` amber, `<40` red).

---

## Project structure

```
FarmersForwardModel/
├── frontend/                     # Vite + React 19 SPA (react-router 7, Leaflet)
│   ├── src/                      #   pages, dashboard, components, styles, api client
│   ├── package.json
│   └── dist/                     #   production build output (npm run build)
├── backend/                      # Flask API (port 5000 dev, gunicorn :8000 prod)
│   ├── app.py                    #   Flask app + all /api endpoints + startup seeding
│   ├── wsgi.py                   #   gunicorn entry point (wsgi:app)
│   ├── config.py                 #   loads ../.env
│   ├── database.py               #   SQLite schema + helpers (farmersforward.db)
│   ├── auth.py                   #   bcrypt, OTP, Resend email delivery, admin seed
│   ├── model_runner.py           #   runs the soil pipeline on submitted sections
│   ├── pdf_report.py             #   ReportLab PDF generation
│   ├── requirements.txt          #   web dependencies
│   └── FarmersForwardModel/      # the trained soil-health ML pipeline
│       ├── pipeline/             #   preprocess, model, agronomy, recommender, reporter
│       ├── models/               #   soil_models.joblib (tracked) + metadata
│       ├── predict.py, train.py
│       └── requirements.txt      #   model (pandas/numpy/scikit-learn/xgboost/...) deps
├── outputs/                      # generated PDF reports (gitignored)
├── nginx.conf                    # production Nginx site config
├── farmersforward.service        # systemd unit for the gunicorn backend
├── .env.example                  # environment template (copy to .env)
└── README.md
```

---

## Prerequisites

- **Python 3.9+** available as `/usr/bin/python3` (3.9.6 is the reference).
- **Node.js 18+** and **npm** (for the Vite frontend).
- **Nginx** and **Certbot** (production only).
- Everything is already installed in the reference dev environment - the steps
  below are for a fresh clone / production host.

---

## 1. Clone

```bash
git clone <your-repo-url> farmersforward
cd farmersforward
```

---

## 2. Backend setup

Install the web dependencies **and** the ML pipeline dependencies. Both are
installed with `--user` so no virtualenv or root is required:

```bash
/usr/bin/python3 -m pip install --user -r backend/requirements.txt
/usr/bin/python3 -m pip install --user -r backend/FarmersForwardModel/requirements.txt
```

Create your environment file from the template and edit as needed:

```bash
cp .env.example .env
# then edit .env - for local dev you can leave every value blank
```

`.env` is loaded from the repo root by `backend/config.py`. In dev you may leave
all keys empty: email delivery is simply skipped and the login OTP is printed
to the server log instead (see [Dev login flow](#dev-login-flow) below).

### Run the backend (dev)

```bash
/usr/bin/python3 backend/app.py
```

This starts Flask on **http://localhost:5000** with debug reload. **Every** boot
prints a credential report, so where the admin password comes from is never
ambiguous:

```
----------------------------------------------------------------------
[admin-auth] startup credential report
[admin-auth]   database file: .../backend/farmersforward.db
[admin-auth]   database existed before this boot: yes
[admin-auth]   .env file: .../.env
[admin-auth]   DEFAULT_ADMIN_USERNAME: 'admin' (from the .env file)
[admin-auth]   DEFAULT_ADMIN_PASSWORD: FOUND in the .env file (13 chars) - ...
[admin-auth]   admins table: 1 row(s)
[admin-auth]   -> Admins already exist, so seeding is a NO-OP on this boot.
...
----------------------------------------------------------------------
```

On the **first** startup, when the admins table is still empty, an account is
seeded and its credentials are printed once:

```
======================================================================
  Farmers Forward - admin account created
  username: admin
  password: <shown only if auto-generated>
======================================================================
```

If you did not set `DEFAULT_ADMIN_PASSWORD`, a random 12-character password is
generated and printed in that banner - **copy it now**, it is not shown again.
Seeding runs **only while the admins table is empty**, so setting
`DEFAULT_ADMIN_PASSWORD` afterwards does not change the stored password; use
`python3 -m backend.reset_admin` to rotate it. The SQLite database is created at
`backend/farmersforward.db`.

---

## 3. Frontend setup

```bash
cd frontend
npm install
```

### Run the frontend (dev)

```bash
npm run dev
```

Vite serves the app on **http://localhost:3000** and proxies `/api/*` to the
backend on `http://localhost:5000`, so both dev servers must be running. Open
<http://localhost:3000>.

### Build the frontend (prod)

```bash
npm run build      # outputs to frontend/dist
npm run preview    # optional: preview the production build locally
```

---

## Dev login flow

The staff login is intentionally hidden from the public site. To reach it:

1. On the public home page, **triple-click the Farmers Forward logo** in the
   top-left corner (three clicks within two seconds). Nothing visible happens -
   this silently opens a 30-second listening window.
2. Within those 30 seconds, **type the passcode** `ADMIN_SECRET_CODE` (default
   **`202611`**). There is no input box and no feedback; the moment the digits
   match you are taken to the login page automatically (no Enter needed). A wrong
   key silently cancels - just triple-click the logo again to restart (which also
   clears anything typed). The passcode must equal `SEQ` in
   `frontend/src/pages/PublicHome.jsx`.
3. Enter the **admin username and password** (the ones printed on first backend
   startup - default username `admin`).
4. A **6-digit OTP** is issued. In production it is emailed via the
   [Resend](https://resend.com) HTTPS API. **In dev, with no `RESEND_API_KEY`
   configured, the OTP is printed to the Flask server log**, e.g.:

   ```
   [DEV] OTP for admin: 482913
   ```

   Enter it in the six OTP boxes. The code expires after 5 minutes and allows a
   limited number of attempts.
5. On success you land on the **dashboard**. Log out from the dashboard to clear
   the session.

---

## 4. Production deployment

The app is served by **Nginx** (static SPA + HTTPS + reverse proxy) with the
Flask backend running under **gunicorn** via **systemd**.

Deploy the code to `/var/www/farmersforward` so the paths in `nginx.conf` and
`farmersforward.service` line up:

```bash
sudo mkdir -p /var/www/farmersforward
sudo rsync -a --exclude node_modules ./ /var/www/farmersforward/
sudo chown -R www-data:www-data /var/www/farmersforward
```

Install backend deps for the service user and build the frontend on the host:

```bash
cd /var/www/farmersforward
sudo -u www-data /usr/bin/python3 -m pip install --user -r backend/requirements.txt
sudo -u www-data /usr/bin/python3 -m pip install --user -r backend/FarmersForwardModel/requirements.txt
(cd frontend && npm install && npm run build)     # produces frontend/dist
```

Create the production `.env` (set a strong `SECRET_KEY`, `ADMIN_SECRET_CODE`,
`RESEND_API_KEY`, and `COOKIE_SECURE=true`):

```bash
sudo cp .env.example .env && sudo nano .env
sudo chown www-data:www-data .env && sudo chmod 600 .env
```

### 4a. systemd (gunicorn backend on 127.0.0.1:8000)

```bash
sudo cp farmersforward.service /etc/systemd/system/farmersforward.service
sudo systemctl daemon-reload
sudo systemctl enable --now farmersforward.service
sudo systemctl status farmersforward.service
sudo journalctl -u farmersforward.service -f   # tail logs (default admin banner + dev OTPs)
```

The unit runs
`/usr/bin/python3 -m gunicorn --workers 3 --bind 127.0.0.1:8000 wsgi:app`
from `WorkingDirectory=/var/www/farmersforward/backend`, reads
`/var/www/farmersforward/.env`, runs as `www-data`, and restarts on failure.

### 4b. Nginx (static SPA + API proxy)

```bash
sudo cp nginx.conf /etc/nginx/sites-available/farmersforward
sudo ln -s /etc/nginx/sites-available/farmersforward /etc/nginx/sites-enabled/farmersforward
sudo mkdir -p /var/www/certbot          # ACME challenge webroot
sudo nginx -t                            # validate the config
sudo systemctl reload nginx
```

Nginx serves the React build from `/var/www/farmersforward/frontend/dist` with an
SPA fallback (`try_files $uri /index.html`) and proxies `/api/` to
`http://127.0.0.1:8000`.

### 4c. TLS with Certbot (Let's Encrypt)

With the port-80 server live (so the ACME HTTP-01 challenge can be answered),
obtain and install certificates:

```bash
sudo certbot --nginx -d farmersforward.org -d www.farmersforward.org
```

Certbot provisions the certificate at
`/etc/letsencrypt/live/farmersforward.org/` (the paths referenced in
`nginx.conf`) and installs an automatic renewal timer. After issuance:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

The site is now live at **https://farmersforward.org**.

### Updating a deployment

```bash
cd /var/www/farmersforward
git pull                                   # or rsync new code
(cd frontend && npm install && npm run build)
sudo systemctl restart farmersforward.service
sudo systemctl reload nginx
```

---

## Configuration reference (`.env`)

| Key | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Flask session cookie signing key | `dev-insecure-change-me` |
| `ADMIN_SECRET_CODE` | Hidden staff-access passcode (typed invisibly; must match `SEQ` in the frontend) | `202611` |
| `RESEND_API_KEY` | OTP via email - the only delivery channel. Get one at [resend.com/api-keys](https://resend.com/api-keys) | unset → email skipped, OTP logged instead |
| `RESEND_FROM` | Sender address. The default test sender needs no domain verification but **only delivers to the Resend account owner's address** | `onboarding@resend.dev` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | **Unused.** Kept only so SMTP delivery can be restored on a host that allows outbound SMTP | port `587`; `FROM` defaults to `SMTP_USER` |
| `DEFAULT_ADMIN_USERNAME` | Seeded admin username (first boot only) | `admin` |
| `DEFAULT_ADMIN_PASSWORD` | Seeded admin password. Applied **only while the admins table is empty**; rotate later with `python3 -m backend.reset_admin` | unset → random, printed once |
| `DEFAULT_ADMIN_PHONE` / `DEFAULT_ADMIN_EMAIL` | Seeded admin contact details | `+10000000000` / `admin@farmersforward.org` |
| `DATABASE_URL` | Postgres connection URI (Supabase). Unset falls back to local SQLite. See [Database](#database) | unset -> SQLite at `backend/farmersforward.db` |
| `COOKIE_SECURE` | Send the session cookie only over HTTPS | `false` (set `true` in prod) |
| `ALLOWED_ORIGINS` | Comma-separated browser origins allowed to call the API with credentials. See [CORS allow-list](#cors-allow-list) | the production + dev origins listed below |
| `FRONTEND_ORIGIN` | **Deprecated** single-origin form. Still honoured and merged into `ALLOWED_ORIGINS` when set | `http://localhost:3000` |

When `RESEND_API_KEY` is **not** set, a created OTP is printed to the server log
(`[DEV] OTP for <user>: <code>`) so the flow is testable without a mail provider.
Once the key is set the code is never logged. It is never returned in any HTTP
response.

Delivery goes over HTTPS rather than SMTP because Render blocks all outbound
SMTP traffic ("Network is unreachable" on both port 587 and 465). Every send
outcome is logged - grep the service logs for `Email OTP`:

```
[auth] Email OTP to admin@… accepted by Resend (id=…).
[auth] Email OTP to admin@… FAILED - HTTP 403 from the Resend API: {"message":"You can only send testing emails to your own email address…"}
```

---

## Database

The backend runs on **Postgres when `DATABASE_URL` is set**, and on a local
**SQLite file when it is not**. Same schema, same queries; only the driver and
the SQL dialect differ. There is no ORM.

### Why Postgres in production

Render's free tier has **no persistent disk**. `backend/farmersforward.db` is
recreated empty on every deploy, which takes the seeded admin account with it.
The boot log says so plainly when it happens:

```
[admin-auth]   database existed before this boot: NO - fresh/empty disk, everything is being re-created
```

Pointing `DATABASE_URL` at Supabase moves the data off the box, so it survives
deploys and restarts.

### Use the pooler, not the direct connection

Supabase gives you two endpoints. Use the **Transaction pooler on port 6543**:

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
```

Not the direct connection on port 5432. gunicorn runs several sync worker
processes and this app opens a connection per query, so the direct endpoint's
free-tier connection cap is reached quickly. The pooler multiplexes those onto a
small number of real backends. `check_db_connection.py` warns if it sees port
5432 on a Supabase host.

Transaction pooling mode does not support session state or prepared statements,
which is fine here: every query is a single autocommitted statement or a short
explicit transaction. `sslmode=require` is appended automatically unless the URL
already specifies an `sslmode`.

If the password contains `@`, `:`, `/` or `?`, URL-encode it or the URI will
parse wrongly.

### Verifying a connection

Set the variable **in your shell**, never in a committed file:

```bash
export DATABASE_URL='postgresql://postgres.xxx:PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres'
python3 -m backend.check_db_connection          # connect, list tables, row counts
python3 -m backend.check_db_connection --init   # create missing tables first
```

It prints the host and database but replaces the password with `***`. With
`DATABASE_URL` unset it reports on the SQLite fallback instead.

Table creation is `CREATE TABLE IF NOT EXISTS` and `init_db()` runs on every
boot, so a fresh Supabase project needs no manual migration step: just start the
app, or run the command above with `--init`.

### Local development

Do nothing. With `DATABASE_URL` unset the app uses SQLite exactly as before, and
the test suite never needs a live database. `psycopg2-binary` is in
`backend/requirements.txt` but is imported only when `DATABASE_URL` is set.

### Schema notes

| | SQLite | Postgres |
| --- | --- | --- |
| primary keys | `INTEGER PRIMARY KEY AUTOINCREMENT` | `INTEGER GENERATED ALWAYS AS IDENTITY` |
| floats | `REAL` | `DOUBLE PRECISION` |
| timestamps | `TEXT`, ISO8601 UTC | `TEXT`, ISO8601 UTC (unchanged) |
| `otps.attempts` / `otps.consumed` | `INTEGER` | `INTEGER` (not `BOOLEAN`) |

Timestamps stay ISO strings rather than becoming `TIMESTAMPTZ`, so the parsing
in `auth.py` is untouched. `attempts` and `consumed` stay integers because the
queries compare them against `0` and `1`, which Postgres rejects on a real
boolean column.

Queries are written once in SQLite's `?` placeholder style and translated to
psycopg2's `%s` inside `backend/database.py`. Writing `%s` in the source would
have broken the SQLite fallback, and `%s` also collides with Python's own `%`
formatting, which this codebase already uses to build SQL identifiers. psycopg2
has no `lastrowid`, so `RETURNING id` is appended to INSERTs in the same place.

## CORS allow-list

The frontend and backend are separate Render services, so every API call is
cross-origin and must be named in the allow-list. `ALLOWED_ORIGINS` is read at
startup from the environment, comma separated:

```
ALLOWED_ORIGINS=https://farmers-forward-1.onrender.com,https://farmersforward.org,https://www.farmersforward.org,http://localhost:3000
```

Leaving it unset falls back to exactly those origins plus `http://127.0.0.1:3000`
(see `DEFAULT_ALLOWED_ORIGINS` in `backend/config.py`), so a fresh deploy serves
production rather than dev only.

| Origin | What it is |
| --- | --- |
| `https://farmers-forward-1.onrender.com` | current Render frontend service |
| `https://farmersforward.org` | intended production domain |
| `https://www.farmersforward.org` | production domain, www |
| `http://localhost:3000` | vite dev server (port set in `frontend/vite.config.js`) |
| `http://127.0.0.1:3000` | same, via loopback IP |

**When a Render service is renamed or recreated**, add the new URL to
`ALLOWED_ORIGINS` in the Render dashboard and restart the service. No code
change or redeploy is needed. This has already happened once:
`farmers-forward` became `farmers-forward-1`.

Two things make a CORS failure hard to spot, so check them first:

- A rejected request still returns **HTTP 200**. The browser discards the
  response because `Access-Control-Allow-Origin` is missing, which surfaces in
  the console as a CORS error and in the UI as a generic network failure.
- The effective list is printed on every boot (`CORS: N allowed origin(s) from
  ...`). Check the Render deploy log before changing anything.

Because the session cookie is sent with every call (`supports_credentials`), a
wildcard `*` is not valid here. Origins must be listed exactly, including scheme
and any port.

Regression tests live in `backend/tests/test_cors.py` and assert the preflight
and the actual response for every `/api` route:

```bash
python -m unittest discover -s backend/tests -v
```

## Troubleshooting

- **Login always says "Invalid credentials"?** Check the
  `[admin-auth] startup credential report` block the backend prints on every
  boot. It names the database file, says whether `DEFAULT_ADMIN_PASSWORD` was
  found in the environment or a random one was generated, and how many admin
  rows exist. Remember that seeding only runs when the admins table is **empty**
  - setting `DEFAULT_ADMIN_PASSWORD` after the account exists changes nothing.
- **Lost the admin password / need to rotate it?** Set `DEFAULT_ADMIN_PASSWORD`,
  then run:
  ```bash
  python3 -m backend.reset_admin --yes      # or omit --yes to confirm interactively
  ```
  This deletes only the `admins` rows (and their orphaned OTP rows) and re-seeds;
  farms, samples, results and contacts are preserved, and the script verifies
  their row counts are unchanged before exiting. Use `--dry-run` to preview, or
  `--password` / `--password-stdin` to set the new password inline. Do **not**
  delete `backend/farmersforward.db` - that wipes all stored field data.
- **Frontend can't reach the API in dev?** Make sure the backend is running on
  port 5000 - the Vite dev server proxies `/api` to `http://localhost:5000`.
- **`nginx -t` fails on cert paths?** Run `certbot` first (step 4c); it creates
  the certificate files referenced in `nginx.conf`.
- **Watch the OTP / admin banner in prod:**
  `sudo journalctl -u farmersforward.service -f`.

### Crops on soil samples

Each sample point requires one crop before submission. In **Data entry**, type
to filter the crop suggestions, use the arrow keys and Enter to choose one, or
enter a custom name (up to 100 characters). The crop autosaves with that point
and appears in review, results, and downloaded reports.

The analysis uses existing crop-specific nitrogen targets and soil profiles
where available. Common suggestions are not a guarantee of profile coverage:
crops without a profile use explicitly labelled general guidance. The trained
soil-health model and its crop classes are unchanged.

On backend startup, `init_db()` adds `samples.crop_type` to existing SQLite and
Postgres databases. Old readings retain a null crop; old saved drafts must have
a crop added to every point before a new submission. Deploy the backend update
before or alongside the frontend so crop values are validated and stored.
