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
  Results), gated behind a hidden staff login: a passcode, then a passwordless
  sign-in with a 6-digit code emailed to a registered admin. Field Setup includes
  a Farm Finder that jumps the map to an address (via OpenStreetMap Nominatim) or
  coordinates.
- **Admins** - any number of admin profiles, each an `owner` or an `admin`.
  Owners add, deactivate and reactivate admins on the Admins page; nobody is
  ever deleted.
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
│   ├── auth.py                   #   email login codes (Resend), roles, owner seed
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
# then edit .env - for local dev only SECRET_KEY and DEFAULT_ADMIN_EMAIL matter
```

`.env` is loaded from the repo root by `backend/config.py`. `SECRET_KEY` is
**required**, in dev too: the backend refuses to boot unless it is at least 32
characters, made of at least 8 distinct characters, and not the old public
default `dev-insecure-change-me`. Use a random value; generate one with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Set `DEFAULT_ADMIN_EMAIL` to your own address so a fresh database gets an owner
you can log in as. Every other key may stay empty: email delivery is simply
skipped and the login code is printed to the server log instead (see
[Dev login flow](#dev-login-flow) below).

### Run the backend (dev)

```bash
/usr/bin/python3 backend/app.py
```

This starts Flask on **http://localhost:5000** with debug reload. **Every** boot
prints a startup admin report, so who can log in, and whether their codes can be
delivered, is never ambiguous:

```
----------------------------------------------------------------------
[admin-auth] startup admin report
[admin-auth]   database: SQLite file .../backend/farmersforward.db
[admin-auth]   database existed before this boot: yes
[admin-auth]   admins: 2 row(s), 2 active; owner(s): ow***@example.com
[admin-auth]   DEFAULT_ADMIN_EMAIL: set (from the .env file)
[admin-auth]   OTP email: NOT configured (codes are printed to this log)
----------------------------------------------------------------------
```

With `RESEND_API_KEY` set, the last line reads
`OTP email: Resend from noreply@example.org` (your `RESEND_FROM`). A `WARNING`
line is added when there is no active owner, or when `RESEND_FROM` is still
Resend's shared test sender, which only delivers to the Resend account owner's
own inbox - other admins would never receive a code. Owner emails are masked.

On the **first** startup, when the admins table is still empty, the first owner
is seeded from `DEFAULT_ADMIN_EMAIL` (display name `DEFAULT_ADMIN_NAME`, or the
part of the email before the @). Seeding runs **only while the admins table is
empty**, so changing `DEFAULT_ADMIN_EMAIL` afterwards changes nothing: owners add
everyone else on the Admins page, and `python3 -m backend.reset_admin --email
you@example.org` restores owner access if it is ever lost (see
[Troubleshooting](#troubleshooting)). A database from the old username/password
login is migrated once, on its first boot of this version (see
[Admin accounts: one-time migration](#admin-accounts-one-time-migration)). The
SQLite database is created at `backend/farmersforward.db`.

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
3. Enter your **email address** - the one an owner registered for you (for the
   first owner, `DEFAULT_ADMIN_EMAIL`). The page answers "If this email is
   registered, a code was sent." for every well-formed address, registered,
   unknown or deactivated alike, so it never reveals who is an admin.
4. A **6-digit code** is issued to a registered, active admin. In production it
   is emailed via the [Resend](https://resend.com) HTTPS API. **In dev, with no
   `RESEND_API_KEY` configured, the code is printed to the Flask server log**,
   e.g.:

   ```
   [DEV] OTP for owner@example.com: 482913
   ```

   Enter it in the six OTP boxes. The code expires after 5 minutes and allows 3
   attempts. A new code can be requested at most once every 60 seconds per
   email (faster requests are silently ignored, so the resend control stays
   disabled for 60 seconds), and after 10 wrong guesses for an email within an
   hour, no new codes are issued for it until the hour has passed.
5. On success you land on the **dashboard**. Owners also have the **Admins**
   page (`/admins`): list every admin, add one (email + display name), and
   deactivate or reactivate them. A deactivated admin is signed out on their
   next request, and the sessions that ended stay dead even if they are
   reactivated later (they sign in afresh); an owner cannot deactivate
   themselves. Log out from the dashboard to clear the session.

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

Create the production `.env`: a strong `SECRET_KEY` (required, at least 32
characters), `ADMIN_SECRET_CODE`, `RESEND_API_KEY`, a `RESEND_FROM` address on a
domain verified in Resend (the default test sender only reaches the Resend
account owner), `DEFAULT_ADMIN_EMAIL` for the first owner, and
`COOKIE_SECURE=true`:

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
sudo journalctl -u farmersforward.service -f   # tail logs (startup admin report + dev OTPs)
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
| `SECRET_KEY` | Flask session cookie signing key. **Required**: the backend refuses to boot if it is unset, shorter than 32 characters, made of fewer than 8 distinct characters, or the old public default `dev-insecure-change-me`. Use a random value, not a phrase: anyone with the public passcode can get a signed cookie to attack it offline. Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`. Changing it logs every admin out | none - required |
| `ADMIN_SECRET_CODE` | Hidden staff-access passcode (typed invisibly; must match `SEQ` in the frontend) | `202611` |
| `RESEND_API_KEY` | OTP via email - the only delivery channel. Get one at [resend.com/api-keys](https://resend.com/api-keys) | unset → email skipped, OTP logged instead |
| `RESEND_FROM` | Sender address. The default test sender needs no domain verification but **only delivers to the Resend account owner's address**, so with more than one admin it must be an address on a domain verified in Resend, or the other admins never receive a code (the startup admin report warns while the test sender is in use) | `onboarding@resend.dev` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | **Unused.** Kept only so SMTP delivery can be restored on a host that allows outbound SMTP | port `587`; `FROM` defaults to `SMTP_USER` |
| `DEFAULT_ADMIN_EMAIL` | Email of the first owner, seeded **only while the admins table is empty** (a fresh database). In the [one-time migration](#admin-accounts-one-time-migration) it also replaces the legacy owner's email when that is missing, malformed, or the old default `admin@farmersforward.org`. Changing it later does nothing; restore owner access with `python3 -m backend.reset_admin --email ...` | unset → no owner is seeded |
| `DEFAULT_ADMIN_NAME` | Display name for that seeded owner, and for the owner created by the one-time migration | unset → the part of the email before the @ (seed), or the old username such as `admin` (migration) |
| `OTP_TTL_SECONDS` | How long a login code stays valid | `300` |
| `OTP_MAX_ATTEMPTS` | Wrong tries before a code locks | `3` |
| `OTP_RESEND_COOLDOWN_SECONDS` | At most one new code per email in this many seconds while the last one can still be used; faster requests are silently ignored (same response). A code already used, locked or expired doesn't hold back the next | `60` |
| `OTP_FAILURE_LIMIT` | Wrong guesses for one email within `OTP_FAILURE_WINDOW_SECONDS` after which no new codes are issued for that email until the window passes | `10` |
| `OTP_FAILURE_WINDOW_SECONDS` | The window `OTP_FAILURE_LIMIT` counts over | `3600` |
| `OTP_FAILURE_LIMIT_PER_DAY` | Wrong guesses for one email within a day after which no new codes are issued; caps a slow trickle that stays under the hourly limit (about 30 guesses a day instead of ~290) | `30` |
| `OTP_ISSUE_LIMIT_PER_HOUR` | New codes per email per hour; bounds how many emails anyone can make an admin receive (and the shared Resend quota they can burn) | `5` |
| `OTP_GLOBAL_LIMIT_PER_HOUR` | New codes per hour across **all** emails. Every login request writes a row, so this keeps a flood of random addresses from filling the database; while it holds, nobody gets a new code and the log says so | `500` |
| `DATABASE_URL` | Postgres connection URI (Supabase). Unset falls back to local SQLite. See [Database](#database) | unset -> SQLite at `backend/farmersforward.db` |
| `COOKIE_SECURE` | Send the session cookie only over HTTPS | `false` (set `true` in prod) |
| `ALLOWED_ORIGINS` | Comma-separated browser origins allowed to call the API with credentials. See [CORS allow-list](#cors-allow-list) | the production + dev origins listed below |
| `FRONTEND_ORIGIN` | **Deprecated** single-origin form. Still honoured and merged into `ALLOWED_ORIGINS` when set | `http://localhost:3000` |

Every `OTP_*` value must be a whole number in a sensible range (for example the cooldown 10-3600 s, the limits at least 1); anything else is logged as a `[config] WARNING` at boot and the default is used, so a typo can't silently lock every admin out.

No longer read: `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD` and
`DEFAULT_ADMIN_PHONE` went away with the password login (as did the `bcrypt`
dependency). Leaving them set does nothing; delete them from the host once this
version is deployed. `APP_ENV` is still read but has no effect.

When `RESEND_API_KEY` is **not** set, the code issued to a registered, active
admin is printed to the server log (`[DEV] OTP for <email>: <code>`) so the flow
is testable without a mail provider. Once the key is set the code is never
logged. It is never returned in any HTTP response.

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
recreated empty on every deploy, which takes every admin with it: only the
`DEFAULT_ADMIN_EMAIL` owner is re-seeded, and everyone added on the Admins page
is gone. The startup admin report says so plainly when it happens:

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
app, or run the command above with `--init`. (`--init` never runs the one-time
admins migration below; only the app's own boot does.)

### Local development

Do nothing. With `DATABASE_URL` unset the app uses SQLite exactly as before, and
the test suite never needs a live database. `psycopg2-binary` is in
`backend/requirements.txt` but is imported only when `DATABASE_URL` is set.

### Schema notes

| | SQLite | Postgres |
| --- | --- | --- |
| primary keys (`farms`, `samples`, `results`, `contacts`) | `INTEGER PRIMARY KEY AUTOINCREMENT` | `INTEGER GENERATED ALWAYS AS IDENTITY` |
| `admins.id` / `otps.id` / `otps.admin_id` | `TEXT` (uuid string) | `UUID` |
| floats | `REAL` | `DOUBLE PRECISION` |
| timestamps | `TEXT`, ISO8601 UTC | `TEXT`, ISO8601 UTC (unchanged) |
| `admins.is_active` | `BOOLEAN` (stored as 0/1) | `BOOLEAN` |
| `otps.attempts` / `otps.consumed` | `INTEGER` | `INTEGER` (not `BOOLEAN`) |

Timestamps stay ISO strings rather than becoming `TIMESTAMPTZ`, so the parsing
in `auth.py` is untouched. `attempts` and `consumed` stay integers because the
queries compare them against `0` and `1`, which Postgres rejects on a real
boolean column. `admins.is_active` is a real boolean on both: queries only ever
bind a Python `bool` to it. Admin and login-challenge ids are uuids generated by
the app.

Queries are written once in SQLite's `?` placeholder style and translated to
psycopg2's `%s` inside `backend/database.py`. Writing `%s` in the source would
have broken the SQLite fallback, and `%s` also collides with Python's own `%`
formatting, which this codebase already uses to build SQL identifiers. psycopg2
has no `lastrowid`, so `RETURNING id` is appended to INSERTs in the same place.

### Admin accounts: one-time migration

`init_db()` also upgrades a database created by the old single-admin
username/password login. When `admins` still has the old shape (`username` /
`password_hash` / `phone` columns), it:

1. renames that table to `admins_legacy`, which is kept for rollback and never
   read by the app;
2. creates the new `admins` table and copies the legacy row(s) in: the oldest
   becomes the **owner**, emails are lowercased, the owner's display name is
   `DEFAULT_ADMIN_NAME` if set (otherwise the old username), and `created_at` is
   kept. When `DEFAULT_ADMIN_EMAIL` is set to a valid address it replaces the
   owner's legacy email if that is missing, malformed, or the old built-in
   default `admin@farmersforward.org`. Without it the old default is kept, with
   a warning (it is on your domain and the old code emailed codes there, so it
   may be a real inbox): the owner is never dropped;
3. drops and recreates `otps`. In-flight login codes are discarded, and every
   existing session is logged out once.

Once the transaction has committed, it logs:

```
[admin-auth] migrated 1 legacy admin row(s) into the new admins table (owner: ow***@example.com); the old table is kept as admins_legacy
```

Every later boot finds the new shape and does nothing, so a second run is a
no-op. Everything runs in one transaction, and on Postgres under an advisory
lock, so a failed boot changes nothing and gunicorn workers booting together
cannot race. Only the app's own boot migrates: the CLI scripts
(`reset_admin`, `check_db_connection --init`) refuse to touch a database that
still has the old schema, so running them from a newer checkout can't break the
deploy that is still serving it. Before the first deploy, check the email on the
current admin row (Supabase table editor, `admins` table): that address has to
be an inbox your `RESEND_FROM` can deliver to, or nobody can sign in as the
owner. After it, the `owner(s):` line of the startup admin report shows it
masked (fix it with `python3 -m backend.reset_admin --email ...`).

On Postgres, row-level security is enabled (with no policies) on **every**
table. Supabase grants its `anon` and `authenticated` roles access to all tables
in the `public` schema, so without it anyone holding the project's anon key
could read and write them through the auto-generated REST API: login codes in
`otps`, visitors' details in `contacts`, all the field data. The app connects as
the tables' owner, which row-level security does not apply to, so it is
unaffected.

**Rollback**, only if the previous deploy must be restored. Order matters:

1. On Render, roll back to the previous deploy and wait until it is live. It
   boots fine on the new schema (its `init_db()` only creates missing tables,
   and its seeding skips a non-empty `admins`); only its logins fail until
   step 2.
2. Then run this once, in the Supabase SQL editor. Pick an unused name for the
   parked table; nothing needs restarting afterwards.

```sql
BEGIN;
DROP TABLE otps;
ALTER TABLE admins RENAME TO admins_v2_20261023;
ALTER TABLE admins_legacy RENAME TO admins;
CREATE TABLE otps (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    admin_id   INTEGER NOT NULL,
    code       TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    consumed   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
ALTER TABLE otps ENABLE ROW LEVEL SECURITY;
COMMIT;
```

This version's session cookie is called `ff_admin_session`, not Flask's default
`session`, so after a rollback the previous version simply ignores it: nobody
signed in here (a deactivated admin included) stays signed in there.

Doing it the other way round leaves this version serving a schema it can't
read, and its next boot would simply migrate again. For the local SQLite file,
stop the app, run the same SQL minus the `CREATE TABLE otps (...)` and
`ALTER TABLE otps ENABLE ROW LEVEL SECURITY` statements with
`sqlite3 backend/farmersforward.db`, and start the previous version: it
recreates its own `otps` table.

Re-deploying this version afterwards re-runs the migration from the legacy row.
Admins added in between stay in the parked table and have to be re-added. Once
you're confident you won't roll back, `DROP TABLE admins_legacy;` removes the
old row (it still holds the retired password hash).

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

- **Backend refuses to start with a `SECRET_KEY` error?** It must be set, at
  least 32 characters long, made of at least 8 distinct characters, and not the
  old public default `dev-insecure-change-me`. Generate one with
  `python3 -c "import secrets; print(secrets.token_hex(32))"`.
- **No login code arrives?** The login page gives the same answer for every
  well-formed email, so look at the server side. The
  `[admin-auth] startup admin report` block printed on every boot says whether
  email is configured (`OTP email:`) and warns when `RESEND_FROM` is still
  Resend's test sender, which only delivers to the Resend account owner. Every
  send is logged (grep for `Email OTP`), and so is a request that was throttled
  or matched no active admin (grep for `login code`). For a read-only report of
  every admin (masked email, display name, role, active, created, last login)
  plus email-delivery warnings, run:
  ```bash
  python3 -m backend.inspect_admin   # exit 0: an active owner exists, 1: none does, 2: error
  ```
  Remember that a new code is issued at most once every 60 seconds per email.
- **Locked out, or no active owner?** Run:
  ```bash
  python3 -m backend.reset_admin --email you@example.org --yes   # or omit --yes to confirm interactively
  ```
  This restores owner access: it creates an active owner with that email, or
  promotes and reactivates the admin who already has it (`--name NAME` also
  renames them). It never deletes or demotes anyone, and farms, samples, results
  and contacts are left untouched. A real run also clears any login throttle
  on that address (someone hammering a known admin email can otherwise keep new
  codes from being issued for as long as they keep at it), so the owner can ask
  for a code straight away. On a database still in the old single-admin
  shape it refuses and changes nothing: deploy first, since only the app's boot
  migrates. Render's free tier has no shell, so run it from your machine with
  `DATABASE_URL` set to the Supabase pooler URL. `--email` defaults to `DEFAULT_ADMIN_EMAIL`;
  use `--dry-run` to preview. Do **not** delete `backend/farmersforward.db` -
  that wipes all stored field data.
- **Frontend can't reach the API in dev?** Make sure the backend is running on
  port 5000 - the Vite dev server proxies `/api` to `http://localhost:5000`.
- **`nginx -t` fails on cert paths?** Run `certbot` first (step 4c); it creates
  the certificate files referenced in `nginx.conf`.
- **Watch the startup admin report / dev codes in prod:**
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
