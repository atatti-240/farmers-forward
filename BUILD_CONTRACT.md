# Farmers Forward - Build Contract (single source of truth)

Every implementation agent MUST read this file fully and conform to it EXACTLY.
The whole app is built by parallel agents writing disjoint files; coherence
depends entirely on the shared names, paths, and shapes below. Do not invent
alternative names. Do not touch files outside your assigned list.

Repo root: `/Users/agasthyatatti/Desktop/FarmersForwardModel`

---

## 0. Environment (already installed - DO NOT run installers or builds)

- Backend interpreter: `/usr/bin/python3` (3.9.6). Installed: flask, flask-cors,
  reportlab, python-dotenv, gunicorn, pandas, numpy, scikit-learn, xgboost,
  matplotlib, joblib.
- Frontend: Vite 8 + React 19 + react-router-dom 7 + leaflet 1.9 + react-leaflet 5,
  already installed under `frontend/node_modules`. `frontend/package.json`,
  `frontend/vite.config.js`, `frontend/index.html` ALREADY EXIST and are correct -
  do NOT modify them unless your task explicitly says so.
- `frontend/src/assets/logo.png` ALREADY EXISTS (the brand logo). Import it; never
  overwrite it.
- The soil model lives at `backend/FarmersForwardModel/` (package `pipeline`,
  trained bundle at `backend/FarmersForwardModel/models/soil_models.joblib`).

Do NOT run `npm`, `pip`, `vite`, or `train.py`. Only write source files.

---

## 1. Brand tokens (use everywhere)

Defined as CSS variables in `frontend/src/styles/theme.css` under `:root`:

```
--ff-blue:        #4AAFE0;   /* primary */
--ff-blue-dark:   #2f8fc0;   /* hover/active */
--ff-blue-soft:   #e8f5fc;   /* tints, backgrounds */
--ff-red:         #E8302A;   /* accent / danger / "poor" */
--ff-red-dark:    #c72620;
--ff-bg:          #F7F9FC;   /* page background */
--ff-surface:     #FFFFFF;   /* cards */
--ff-text:        #1A1A2E;   /* body text */
--ff-text-muted:  #5b6472;
--ff-border:      #e2e8f0;
--ff-green:       #2E9E5B;   /* health good */
--ff-amber:       #F5A623;   /* health medium */
--ff-radius:      12px;
--ff-radius-sm:   8px;
--ff-shadow:      0 2px 14px rgba(26,26,46,0.08);
--ff-shadow-lg:   0 8px 30px rgba(26,26,46,0.14);
--ff-font:        'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
```

Health-score → color rule (used by backend AND frontend, keep identical):
`score >= 70` → green `#2E9E5B`; `40 <= score < 70` → amber `#F5A623`;
`score < 40` → red `#E8302A`. The backend keeps these buckets in
`result.color`; the dashboard maps a score onto a smooth red → amber → green
gradient through the same three colors (`frontend/src/dashboard/soilColor.js`:
`scoreColor(score)`, stops at 20/55/85 = each bucket's midpoint, blended in
HSL). Every map marker, grid-cell shade and score card uses `scoreColor`, so
one point reads the same colour in steps 2 and 4. No score yet → `#9aa3af`.

---

## 2. Backend (owned by agent: BACKEND)

Runs on Flask, port **5000** in dev. All routes under `/api`. Uses Flask server
sessions (signed cookie). SQLite DB at `backend/farmersforward.db`.

### 2.1 Files
- `backend/config.py` - load `.env` (python-dotenv, from repo root), expose config.
- `backend/database.py` - SQLite connection + schema init + query helpers.
- `backend/auth.py` - passwordless login codes (issue, verify, throttle), email
  via the Resend HTTPS API, `current_admin()`, the `login_required` /
  `owner_required` decorators, admin management, first-owner seeding and the
  startup admin report.
- `backend/model_runner.py` - run the soil pipeline on submitted sections.
- `backend/pdf_report.py` - ReportLab PDF generation.
- `backend/app.py` - Flask app + all endpoints + startup seeding + `main`.
- `backend/wsgi.py` - `from app import app` (gunicorn entry `wsgi:app`).
- `backend/requirements.txt` - web deps list (flask, flask-cors, reportlab,
  python-dotenv, gunicorn) plus a note that the model deps come from
  `FarmersForwardModel/requirements.txt`.
- `backend/reset_admin.py`, `backend/inspect_admin.py`,
  `backend/check_db_connection.py` - operator CLI scripts (§2.8).

### 2.2 config.py
Load `.env` from repo root (`os.path.join(here, '..', '.env')`). Expose (env → default):
- `SECRET_KEY` → **no default; REQUIRED.** The app refuses to boot (raises at
  startup, before anything else runs) if it is unset/empty, shorter than 32
  characters, made of fewer than 8 distinct characters, or equal to the old
  public default `'dev-insecure-change-me'`.
  Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`.
  Changing it logs everyone out.
- `ADMIN_SECRET_CODE` → default `'202611'` (typed invisibly on the public page -
  see the secret-access flow; must match `SEQ` in PublicHome.jsx)
- `RESEND_API_KEY` → None (unset: nothing is emailed; see the dev fallback in
  §2.4). `RESEND_FROM` → `'onboarding@resend.dev'`, Resend's shared test sender,
  which only delivers to the Resend account owner's own inbox: with more than
  one admin it MUST be an address on a domain verified in Resend, or the other
  admins never receive a code.
- `SMTP_HOST`, `SMTP_PORT` (int, default 587), `SMTP_USER`, `SMTP_PASSWORD`,
  `SMTP_FROM` (default SMTP_USER) → None. Unused; kept only to make an SMTP
  revert easy (Render blocks outbound SMTP).
- `DATABASE_PATH` → `backend/farmersforward.db` (absolute, computed)
- `OUTPUTS_DIR` → repo `outputs/` (absolute)
- `MODEL_DIR` → `backend/FarmersForwardModel` (absolute)
- `DEFAULT_ADMIN_EMAIL` → None (no built-in default). Used ONLY (a) to seed the
  first owner when the `admins` table is empty (a fresh database), and (b) as
  the owner's email in the one-time migration (§2.3) when it is valid and the
  legacy owner's email is missing, malformed, or the old default
  `'admin@farmersforward.org'` (without it that old default is kept, with a
  warning: the owner is never dropped).
- `DEFAULT_ADMIN_NAME` → None. Display name for that seeded owner and for the
  owner created by the one-time migration (which otherwise keeps the old
  username, e.g. `'admin'`).
- `SESSION_COOKIE_SECURE` → bool from env `COOKIE_SECURE` (default False in dev)
- `FRONTEND_ORIGIN` → `'http://localhost:3000'`
- Every `OTP_*` setting is a whole number within bounds (`_get_bounded_int`);
  anything else logs a `[config] WARNING` and uses the default.
- `OTP_TTL_SECONDS` → 300; `OTP_MAX_ATTEMPTS` → 3
- `OTP_RESEND_COOLDOWN_SECONDS` → 60: at most one new code per email per
  cooldown while the last code can still be used (faster requests are silently
  ignored; a used, locked or expired code doesn't block the next)
- `OTP_FAILURE_LIMIT` → 10; `OTP_FAILURE_WINDOW_SECONDS` → 3600: once an email
  has that many wrong guesses within the window, no new codes are issued for it
  until the window passes
- `OTP_FAILURE_LIMIT_PER_DAY` → 30: the same, counted over a day, so a trickle
  that stays under the hourly limit is capped too
- `OTP_ISSUE_LIMIT_PER_HOUR` → 5: new codes per email per hour
- `OTP_GLOBAL_LIMIT_PER_HOUR` → 500: new codes per hour across all emails (every
  request writes a row, so this bounds the table under a flood)
- `APP_ENV` → `'development'`. Parsed, but nothing branches on it:
  `APP_ENV=production` does NOT make startup stricter.

No longer read: `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`,
`DEFAULT_ADMIN_PHONE` (password login is gone, and with it the bcrypt
dependency).

### 2.3 database.py schema (create IF NOT EXISTS on init)
```
admins(   id TEXT PRIMARY KEY,               -- uuid string (UUID on Postgres)
          email TEXT NOT NULL UNIQUE CHECK (email = lower(email)),
          display_name TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'admin', -- 'owner' | 'admin' today; free text for future roles
          is_active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TEXT NOT NULL,          -- ISO8601 UTC
          last_login_at TEXT,                -- NULL until the first login
          sessions_valid_after TEXT )        -- stamped on deactivation: sessions
                                             -- that signed in earlier stay dead,
                                             -- even after a reactivation

otps(     id TEXT PRIMARY KEY,               -- uuid; one row = one login challenge
          email TEXT NOT NULL,
          admin_id TEXT REFERENCES admins(id), -- NULL: unknown/deactivated email
          code TEXT NOT NULL,
          expires_at TEXT NOT NULL,          -- ISO8601 UTC
          attempts INTEGER NOT NULL DEFAULT 0,
          consumed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL )

farms(    id INTEGER PRIMARY KEY AUTOINCREMENT,
          farm_name TEXT NOT NULL,
          date TEXT NOT NULL,
          n_sections INTEGER NOT NULL,
          mean_health_score REAL,
          result_json TEXT NOT NULL,         -- full analysis response JSON
          created_at TEXT NOT NULL )

samples(  id INTEGER PRIMARY KEY AUTOINCREMENT,
          farm_id INTEGER NOT NULL,
          sample_id TEXT NOT NULL,
          section_label TEXT NOT NULL,
          crop_type TEXT,  -- NULL only for readings saved before crop selection
          latitude REAL NOT NULL, longitude REAL NOT NULL,
          ph REAL, ec REAL, nitrogen REAL, phosphorus REAL, potassium REAL )

results(  id INTEGER PRIMARY KEY AUTOINCREMENT,
          farm_id INTEGER NOT NULL,
          section_label TEXT NOT NULL,
          health_score REAL, health_category TEXT,
          viable_crops TEXT, recommendations TEXT )  -- JSON strings

contacts( id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL, email TEXT NOT NULL, message TEXT NOT NULL,
          created_at TEXT NOT NULL )
```
Provide: `get_db()` (sqlite3 connection, `row_factory = sqlite3.Row`, `PRAGMA
foreign_keys=ON`), `init_db()`, and small helpers as needed. Use timestamps in
ISO8601 UTC (`datetime.now(timezone.utc).isoformat()`).

`admins` and `otps` are keyed by uuid strings the app generates (`UUID` columns
on Postgres, `TEXT` on SQLite). Admins are deactivated, never deleted. An
`otps` row is written for EVERY login request, including unknown and
deactivated emails (`admin_id` NULL, never emailed), so the responses are
indistinguishable; rows older than a day are pruned.

**One-time migration**, inside `init_db()` on every boot. It is idempotent (a
second run is a no-op), runs in the same single transaction as the rest of
`init_db()`, and is advisory-locked on Postgres, so concurrent gunicorn workers
can't race. Only the app's boot migrates (`init_db(migrate=True)`); the CLI
scripts call `init_db(migrate=False)`, which raises `LegacySchemaError` before
writing anything when the database still has the old shape. If `admins` still
has the old shape (`username` / `password_hash` / `phone` columns):
1. rename it to `admins_legacy` (kept for rollback, never read by the app);
2. create the new `admins` and copy the legacy row(s) in: the oldest becomes
   role `'owner'` (any others `'admin'`); email lowercased (DEFAULT_ADMIN_EMAIL
   if the owner's row has no usable email; other such rows are skipped and stay
   in `admins_legacy`); the owner's display_name = DEFAULT_ADMIN_NAME,
   else the old username (other rows keep their username); created_at
   preserved;
3. drop and recreate `otps`. In-flight login codes are discarded, and every
   existing session is logged out once.

After COMMIT it prints:
```
[admin-auth] migrated 1 legacy admin row(s) into the new admins table (owner: ow***@example.com); the old table is kept as admins_legacy
```

Postgres: row-level security is enabled (no policies) on EVERY table (all of
`TABLES` plus `admins_legacy`), so Supabase's auto-generated REST API can't read
or write them with the anon key. The app connects as the tables' owner and is
unaffected. Only tables without it are altered, so a normal boot takes no lock.

Rollback (only if the previous deploy must be restored): FIRST roll back to the
previous deploy on Render and wait until it is live (it boots on the new schema;
only its logins fail), THEN run once:
```
BEGIN;
DROP TABLE otps;
ALTER TABLE admins RENAME TO admins_v2_20261023;   -- any unused name
ALTER TABLE admins_legacy RENAME TO admins;
CREATE TABLE otps (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  admin_id INTEGER NOT NULL, code TEXT NOT NULL, expires_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
ALTER TABLE otps ENABLE ROW LEVEL SECURITY;
COMMIT;
```
The other order would leave the new code serving a schema it can't read, and its
next boot would migrate again. Re-deploying the new code afterwards re-runs the
migration from the legacy row; admins added in
between stay in the parked table (`admins_v2_20261023` above, or whatever name
you chose) and have to be re-added.

### 2.4 auth.py
Login is passwordless (no passwords, no bcrypt): the passcode gate, then an
email, then the 6-digit code emailed to it. A signed-in admin's profile is
`{id, email, display_name, role}` with `role` `'owner'` or `'admin'`.
- `current_admin() -> dict | None`: the signed-in admin's profile. It re-reads
  the `admins` row on EVERY request and returns None (and drops `authed` /
  `admin_id` from the session) unless `session['authed'] is True`,
  `session['admin_id']` is the uuid of an ACTIVE admin, and, if that admin has
  a `sessions_valid_after`, `session['login_at']` is not earlier. This is what
  makes deactivation immediate (and permanent for the sessions it ended, since a
  copied cookie can't be revoked any other way), and what logs out a
  pre-migration session. Deactivation stamps `sessions_valid_after` and voids
  the admin's outstanding codes.
- `login_required` decorator: 401 `{'error':'auth required'}` unless
  `current_admin()` returns an admin.
- `owner_required` decorator: 401 `{'error':'auth required'}` when not signed
  in; 403 `{'error':'owner only'}` when signed in with a role other than
  `'owner'`.
- `generate_otp() -> str` (6 digits, zero-padded, use `secrets`).
- `request_login_code(email)` (email already trimmed, lowercased, valid):
  writes an `otps` challenge for EVERY call - `admin_id` = the ACTIVE admin
  with that email, else NULL - with expiry = now + OTP_TTL_SECONDS, consuming
  any earlier unconsumed code for that email first (one live code per email).
  Only a challenge with an admin is emailed, and the send starts only after the
  response has been written (`call_on_close`), so not even GIL contention from
  the sending thread shows in the response time: nothing observable depends on
  whether the email is registered.
- Throttling, per email and identical for unknown emails: at most one new code
  per OTP_RESEND_COOLDOWN_SECONDS while the last one is still usable (a faster
  request is silently ignored, the code already sent stays valid, and the HTTP
  response is unchanged); once the wrong guesses for that email within
  OTP_FAILURE_WINDOW_SECONDS reach OTP_FAILURE_LIMIT, or those within a day
  reach OTP_FAILURE_LIMIT_PER_DAY, no new codes are issued for it;
  at most OTP_ISSUE_LIMIT_PER_HOUR codes per email per hour. Across all emails,
  at most OTP_GLOBAL_LIMIT_PER_HOUR codes per hour (while it holds nobody gets
  a new code, which keeps registered and unknown emails indistinguishable).
  Requests for one email are serialized (Postgres advisory lock; SQLite's write
  lock), so the cooldown can't be raced.
- Email via the Resend HTTPS API only if RESEND_API_KEY is set; else skip.
  **Dev fallback:** with no RESEND_API_KEY, print `"[DEV] OTP for %s: %s" %
  (email, code)` instead (e.g. `[DEV] OTP for owner@example.com: 482913`) so the
  flow is testable without a mail provider. Once the key is set the code is
  never logged. (Never return the code in any HTTP response.)
- `verify_login_code(email, code) -> (result, admin_id)`: result is one of
  `'ok'`, `'invalid'`, `'expired'`, `'locked'`. Load the latest unconsumed
  challenge for the email. If none or expired → `'expired'`. Then claim an
  attempt atomically BEFORE comparing (`UPDATE ... SET attempts = attempts + 1
  WHERE ... attempts < OTP_MAX_ATTEMPTS`); no slot → `'locked'`, so racing
  guesses can't beat the cap. If the code matches and the challenge's admin is
  still active → mark consumed (handing the attempt back), stamp
  `last_login_at`, return `'ok'` with the admin id. Else `'locked'` if attempts
  now >= max, otherwise `'invalid'`. A challenge with no admin behaves exactly like a
  real one but can never succeed. `attempts_remaining(email) -> int`.
- Admin management (backs §2.7 `/api/admins`): `list_admins()` (oldest first),
  `create_admin(email, display_name)` (role `'admin'`, active; ValueError with
  an API-ready message on invalid input, `AdminExists` when the email is
  already registered in any state), `set_admin_active(admin_id, bool)` (never
  deletes; deactivating also voids that admin's outstanding code), and
  `ensure_owner(email, display_name=None)` for `reset_admin` (§2.8).
- `seed_owner()`: if `admins` is EMPTY and DEFAULT_ADMIN_EMAIL is set, create
  one active owner with that email and display name DEFAULT_ADMIN_NAME (else
  the part of the email before the @), and print a one-time banner with the
  masked email. A non-empty table is never touched; with DEFAULT_ADMIN_EMAIL
  unset nothing is created (the report below warns).
- `log_admin_status()`: printed on EVERY boot, after seeding:
  ```
  ----------------------------------------------------------------------
  [admin-auth] startup admin report
  [admin-auth]   database: <backend description>
  [admin-auth]   admins: 2 row(s), 2 active; owner(s): ow***@example.com
  [admin-auth]   DEFAULT_ADMIN_EMAIL: set (from the process environment) | unset
  [admin-auth]   OTP email: Resend from noreply@example.org | NOT configured (codes are printed to this log)
  ----------------------------------------------------------------------
  ```
  plus WARNING lines when there is no active owner, or when RESEND_FROM is the
  shared test sender. On SQLite it also says whether the database file existed
  before this boot. It never prints a full email address or any key.

### 2.5 model_runner.py
Add `MODEL_DIR` to `sys.path`, import `pipeline` modules, load the joblib bundle
ONCE (module-level lazy cache). Reuse the logic in
`backend/FarmersForwardModel/predict.py::analyze_sample` (ML score via
`model.predict_frame`, rule problems via `agronomy.detect_problems`, recs via
`recommender.build_recommendations`, crop viability via
`agronomy.crop_viability`).

Public function:
```
run_analysis(farm_name: str, date: str, sections: list[dict], farm_id=None) -> dict
```
`sections` = list of `{'section_label','crop_type','latitude','longitude','ph','ec',
'nitrogen','phosphorus','potassium'}`. Build a DataFrame with columns
`sample_id`(=f"{farm_id or 'F'}-{section_label}"), `latitude`, `longitude`,
`region`='fort_bend', `zone`=section_label, and the five measurements; run
`preprocess.build_features` + `model.predict_frame`. Return EXACTLY:
```
{
  "farm_id": farm_id,
  "farm_name": farm_name,
  "date": date,
  "field_summary": {
     "mean_health_score": float, "health_category": str,
     "min_health_score": float, "max_health_score": float,
     "dominant_problem_code": str, "dominant_problem_label": str,
     "n_sections": int
  },
  "results": [
    {
      "section_label": str, "sample_id": str,
      "crop_type": str,
      "selected_crop": {profile_name, profile_available, fit, within_profile, limiting_factors,
                        note, yield_goal, am_table, basis},  # fit: ideal|viable_with_adjustments|not_viable
      "crop_assessment": { ... crop_data/crop_lookup.assess() + yield_goal ... } | null,
      "latitude": float, "longitude": float,
      "inputs": {"ph":f,"ec":f,"nitrogen":f,"phosphorus":f,"potassium":f,
                 "ece":f, "ec_method":"1:2"},   # ec as entered; ece = what the models used
      "health_score": float,            # headline, rounded 0-100: the crop-specific rule
                                        # score for an A&M-mapped crop, else the ML score
      "health_category": str,
      "score_basis": "crop|general",
      "general_health_score": float,    # ML score ("General soil health"), always present
      "general_health_category": str,
      "rule_health_score": float,
      "color": {"bucket": "green|yellow|red", "hex": "#RRGGBB"},
      "primary_problem": {"code":str,"label":str,"severity":str,"message":str},
      "problems": [ ... agronomy problem dicts ... ],
      "recommendations": [ {title,action,rate,cost_estimate,timing,notes,severity,fix_code} ],
      "crop_suitability": {"viable_now":[str],             # ideal + viable_with_adjustments
                           "ideal":[str], "viable_with_adjustments":[str],
                           "not_viable":[{"crop":str,"limiting_factors":[str]}]},
      "ml_crop_suitable": [str],
      "cost_benefit_experimental": { ... recommender.estimate_cost_benefit ... }
    }, ...
  ]
}
```
Provide helper `score_color(score) -> {'bucket','hex'}` using the rule in §1 and
reuse `reporter.problem_label` for labels. Import label helper from
`pipeline.reporter`.

### 2.6 pdf_report.py (ReportLab)
- `build_section_pdf(section: dict, farm_name: str, date: str) -> bytes` - one
  section. Include: Farmers Forward header (brand blue), farm name, date, section
  label, lat/long, input values table (pH/EC/N/P/K), health score (big, colored by
  §1 rule) + category, crop recommendations (viable now list), and action items
  (each recommendation: title, action, rate, cost, timing). Footer disclaimer.
- `build_combined_pdf(analysis: dict) -> bytes` - cover page (farm name, date,
  field summary, mean score) then one section per page (reuse the section layout).
Return `bytes` (use `io.BytesIO`, `SimpleDocTemplate`). Brand colors via
`reportlab.lib.colors.HexColor`.

### 2.7 app.py - endpoints (all JSON unless noted)
Set `app.secret_key = SECRET_KEY` once it has passed the §2.2 check (no valid
key, no boot), `SESSION_COOKIE_NAME='ff_admin_session'` (not Flask's default
`session`: the previous version, the rollback target, would accept this
version's cookies, a deactivated admin's included), `SESSION_COOKIE_SAMESITE='Lax'`,
`SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE`. Enable CORS
(`flask_cors.CORS(app, supports_credentials=True, origins=[FRONTEND_ORIGIN])`).
At import/startup call `init_db()` (which runs the one-time migration, §2.3),
then `seed_owner()`, then `log_admin_status()`. `if __name__ ==
'__main__': app.run(port=5000, debug=True)`.

| Method | Path | Auth | Request JSON | Success (200) | Errors |
|---|---|---|---|---|---|
| POST | `/api/auth/verify-code` | none | `{code}` | `{ok:true}` sets `session['secret_ok']=True` | wrong code → `{ok:false}` (still 200, no session) |
| POST | `/api/auth/login` | needs `session['secret_ok']` | `{email}` | `{ok:true, message:'If this email is registered, a code was sent.'}` for ANY well-formed email - registered, unknown or deactivated alike; calls `request_login_code(email)` and sets `session['pending_email']` (identical in every case) | no secret_ok → 403 `{error:'forbidden'}`; malformed email → 400 `{error:'Please enter a valid email address'}` |
| POST | `/api/auth/otp` | needs `session['pending_email']` | `{code}` | `{ok:true}` clears the session, then sets `session['authed']=True`, `session['admin_id']` (uuid) and `session['login_at']` (ISO UTC); `last_login_at` is stamped | no pending → 401 `{error:'no pending login'}`; result `invalid`→401 `{error:'invalid',attempts_remaining:n}`; `expired`→401 `{error:'expired'}`; `locked`→423 `{error:'locked'}` and clear pending (keep `secret_ok`) |
| POST | `/api/auth/logout` | none | - | `{ok:true}` clears session | - |
| GET | `/api/auth/me` | none | - | `{authed:true, admin:{id,email,display_name,role}}` or `{authed:false}` (also for a deactivated admin) | - |
| POST | `/api/submit` | login_required | `{farm_name,date,sections:[{section_label,crop_type,latitude,longitude,ph,ec,nitrogen,phosphorus,potassium}]}` | the run_analysis dict (with farm_id) | 400 `{error}` if missing/empty sections |
| POST | `/api/score` | login_required | `{sections:[{section_label,ph,ec,nitrogen,phosphorus,potassium,crop_type?}]}` (<= 500, no coords; crop optional, makes the score crop-specific like /api/submit) | `{scores:[{section_label,health_score,health_category}]}` | 400 `{error}` if a measurement is missing/non-finite |
| GET | `/api/results/<int:farm_id>` | login_required | - | stored analysis dict | 404 `{error:'not found'}` |
| GET | `/api/history` | login_required | - | `{farms:[{farm_id,farm_name,date,created_at,n_sections,mean_health_score}]}` | - |
| POST | `/api/contact` | none | `{name,email,message}` | `{ok:true}` | 400 `{error}` if any empty or email invalid |
| GET | `/api/report/<int:farm_id>/section/<path:section_label>` | login_required | - | `application/pdf` (attachment) | 404 if farm/section missing |
| GET | `/api/report/<int:farm_id>/all` | login_required | - | `application/pdf` (attachment) | 404 |
| GET | `/api/admins` | owner_required | - | `{admins:[{id,email,display_name,role,is_active,created_at,last_login_at}], warnings:[str]}`, oldest first; `warnings` lists email-delivery problems that would stop new admins receiving codes (e.g. RESEND_FROM still the test sender) | not signed in → 401 `{error:'auth required'}`; not an owner → 403 `{error:'owner only'}` |
| POST | `/api/admins` | owner_required | `{email,display_name}` | **201** `{admin:{...same fields}}` (role `'admin'`, active) | invalid email/display_name → 400 `{error}`; duplicate → 409 `{error:'An admin with that email already exists'}`; 401/403 as above |
| PATCH | `/api/admins/<id>` | owner_required | `{is_active}` (JSON boolean) | `{admin:{...same fields}}` | own row with `false` → 400 `{error:"You can't deactivate yourself"}`; leaving no active owner (two owners deactivating each other at once) → 400 `{error:'That would leave no active owner'}`; non-boolean → 400 `{error:'is_active must be true or false'}`; unknown id → 404 `{error:'not found'}`; 401/403 as above |

Every `login_required` route answers 401 `{error:'auth required'}` when there
is no valid session or the admin has been deactivated (checked on every
request, so deactivation is immediate). Admin fields: `id` is a uuid string;
`created_at` / `last_login_at` are ISO-8601 UTC strings such as
`'2026-10-23T18:41:35.123456+00:00'`, and `last_login_at` is null until the
first login; `is_active` is a JSON boolean. `display_name`: trimmed, 1-100
characters, no control characters. `email`: trimmed and lowercased by the
server, at most 254 characters. Nothing is ever deleted, `is_active` is the
only field PATCH changes, and an owner cannot deactivate themselves.

`/api/submit` must: validate, insert `farms` (result_json after analysis) +
`samples` + `results` rows, run `model_runner.run_analysis(...,farm_id)`, return
the analysis. PDF endpoints load `farms.result_json`, pick the section (or all),
call pdf_report, and `send_file`/Response with
`Content-Disposition: attachment; filename=...`.

Email validity: simple regex `^[^@\s]+@[^@\s]+\.[^@\s]+$` (contact form, login
and `/api/admins`; the last two also cap it at 254 characters with no control
characters).

### 2.8 CLI scripts (`backend/`, run from the repo root)
- `python3 -m backend.reset_admin --email EMAIL [--name NAME] [--yes]
  [--dry-run]` - "restore owner access". Creates an active owner with that
  email, or promotes/reactivates the existing admin with that email (renaming
  them if `--name` is given). It never deletes or demotes anyone, and leaves
  farms / samples / results / contacts untouched. `--email` defaults to
  DEFAULT_ADMIN_EMAIL; `--yes` skips the confirmation prompt; `--dry-run`
  changes nothing (it doesn't even create tables). It creates missing tables
  with `init_db(migrate=False)` and refuses (exit 1, nothing written) a
  database that still has the old single-admin schema: only the app's first
  boot migrates.
- `python3 -m backend.inspect_admin` - read-only report of every admin (masked
  email, display name, role, active, created, last login) plus email-delivery
  warnings. Exit 0 if at least one active owner exists, 1 if not, 2 on error.
- `python3 -m backend.check_db_connection [--init]` - unchanged: connectivity,
  tables and row counts for the configured database (`--init` creates missing
  tables first, but never runs the one-time migration). It says when `admins`
  still has the old shape and labels `admins_legacy` as kept for rollback.

---

## 3. Frontend - shared foundation (owned by agent: FOUNDATION)

### 3.1 Files owned
- `frontend/src/main.jsx` - mount, import styles + `leaflet/dist/leaflet.css`,
  apply the Leaflet default-icon fix (see 3.6), wrap `<App/>` in `<BrowserRouter>`.
- `frontend/src/App.jsx` - routes + guards (default export `App`).
- `frontend/src/styles/theme.css` - the `:root` tokens from §1.
- `frontend/src/styles/global.css` - reset + base + the utility classes in §3.4.
- `frontend/src/api/client.js` - API functions (§3.3).
- `frontend/src/api/admins.js` - the owner-only admin API (§4.3), kept out of
  client.js so the multi-admin feature adds a module instead of editing the
  shared one.
- `frontend/src/components/Logo.jsx` - `Logo` (default export) per §3.5.
- `frontend/src/components/RequireAuth.jsx` - `RequireAuth` guard (default export).
- `frontend/src/components/AdminContext.jsx` - `AdminContext` + `useAdmin()`
  (named exports): the signed-in admin's profile, provided by `RequireAuth`.
- `frontend/src/components/AdminNav.jsx` - `AdminNav` (default export), the
  private area's shared sticky header (§5).
- `frontend/src/components/Spinner.jsx` - `Spinner` (default export), a brand
  spinner (class `ff-spinner`).

Delete the placeholder `frontend/src/App.css` import if present; `main.jsx` should
import `./styles/theme.css` then `./styles/global.css` (and leaflet css). Keep
`frontend/src/index.css` minimal or remove its use.

### 3.2 Routes (react-router-dom v7, `<BrowserRouter>` in main.jsx)
In `App.jsx`:
```
<Routes>
  <Route path="/" element={<PublicHome/>} />
  <Route path="/login" element={<Login/>} />
  <Route path="/otp" element={<Otp/>} />
  <Route path="/dashboard" element={<RequireAuth><Dashboard/></RequireAuth>} />
  <Route path="/admins" element={<RequireAuth ownerOnly><Admins/></RequireAuth>} />
  <Route path="*" element={<Navigate to="/" replace/>} />
</Routes>
```
Imports (EXACT default-export paths the other agents will provide):
`./pages/PublicHome.jsx`, `./pages/Login.jsx`, `./pages/Otp.jsx`,
`./pages/Dashboard.jsx`, `./pages/Admins.jsx`. `/login` and `/otp` do their OWN
guard redirects internally (see §4/§5) - App does not guard them; App only
wraps `/dashboard` and `/admins` with `RequireAuth`.

`RequireAuth`: on mount call `api.me()`; while pending render `<Spinner/>`
centered; if `authed` render children inside `<AdminContext.Provider
value={admin}>` (pages read the profile with `useAdmin()`); else `<Navigate
to="/login" replace/>`. With `ownerOnly`, a signed-in admin whose `role` is not
`'owner'` gets `<Navigate to="/dashboard" replace/>` instead. This is UX only;
the server enforces owner-only routes with 403.

### 3.3 api/client.js (all use `fetch`, base path `/api`, `credentials:'include'`)
Provide a default export object `api` AND named exports, with these functions:
```
verifyCode(code)            -> POST /api/auth/verify-code {code}      -> {ok}
login(email)                -> POST /api/auth/login {email}           -> {ok,message}  (throws on 400/403)
verifyOtp(code)             -> POST /api/auth/otp {code}              -> {ok} (throws w/ payload on !ok)
logout()                    -> POST /api/auth/logout                 -> {ok}
me()                        -> GET  /api/auth/me                      -> {authed, admin?:{id,email,display_name,role}}
submitField({farm_name,date,sections}) -> POST /api/submit           -> analysis
scoreSections(sections)     -> POST /api/score                       -> {scores}
getResults(farmId)          -> GET  /api/results/:id                 -> analysis
getHistory()                -> GET  /api/history                     -> {farms}
sendContact({name,email,message}) -> POST /api/contact               -> {ok} (throws on 400)
reportSectionUrl(farmId, label) -> string `/api/report/${farmId}/section/${encodeURIComponent(label)}`
reportAllUrl(farmId)        -> string `/api/report/${farmId}/all`
```
Implement a `request(path, {method, body})` helper: JSON headers, parse JSON,
on `!res.ok` throw an `Error` whose `.status` and `.payload` are set (so callers
can read `err.payload.error`, `err.payload.attempts_remaining`, etc.). `verifyCode`
should NOT throw on `{ok:false}` (it's a 200). `login`/`verifyOtp` should throw on
non-2xx so pages can show messages. A 401 from any path outside `/auth/` means
the session has ended (logged out elsewhere, or the admin was deactivated), so
`request` also sends the browser to `/` (unless it is already there) before
throwing. `login` resolving never means the email is registered or that a code
was sent.

### 3.4 global.css utility classes (define ALL of these; pages rely on them)
- Layout: `.ff-container` (max-width 1120px, centered, padding), `.ff-section`
  (vertical padding 72px), `.ff-row` (flex), `.ff-col` (flex column), `.ff-spacer`.
- Buttons: `.ff-btn` (base), `.ff-btn--primary` (blue bg white text),
  `.ff-btn--secondary` (white bg, blue border/text), `.ff-btn--danger` (red),
  `.ff-btn--ghost`, `.ff-btn:disabled` (dimmed, not-allowed). Rounded, shadow on
  primary, hover uses `--ff-blue-dark`/`--ff-red-dark`.
- Cards: `.ff-card` (surface bg, radius, shadow, padding), `.ff-card__title`.
- Forms: `.ff-field` (wrapper), `.ff-label`, `.ff-input` (full-width, border,
  radius, focus ring in blue), `.ff-input--error` (red border), `.ff-help`,
  `.ff-error-text` (red).
- Progress: `.ff-progress` (track), `.ff-progress__bar` (blue fill, width via
  inline style).
- Badges/pills: `.ff-badge`, and health variants `.ff-badge--green`,
  `.ff-badge--amber`, `.ff-badge--red` (bg tints + colored text).
- Stepper: `.ff-stepper`, `.ff-step`, `.ff-step--active`, `.ff-step--done`,
  `.ff-step__num`.
- Nav: `.ff-nav` (sticky top, white, shadow), `.ff-nav__inner` (container flex,
  space-between).
- Table: `.ff-table` (full width, header row blue-soft, cell borders).
- Spinner: `.ff-spinner` (rotating brand ring; keyframes `ff-spin`).
- Map: `.ff-map` (height 460px, radius, overflow hidden, border).
Use only CSS (no CSS frameworks). Style base `body` with `--ff-bg`/`--ff-text`/
`--ff-font`; `*{box-sizing:border-box}`; headings colors `--ff-text`.

### 3.5 Logo component
`import logoUrl from '../assets/logo.png'`. Props: `variant` (`'public'` |
`'dashboard'`), `onSecretUnlock` (function, only used when public). Render an
`<img src={logoUrl} alt="Farmers Forward" height={40}/>` plus wordmark text
"Farmers Forward" (brand text). Behavior:
- `variant='dashboard'`: wrapper `onClick` → `useNavigate()('/')`; cursor pointer.
- `variant='public'`: detect **3 clicks within 2000ms** on the logo; on the 3rd
  call `onSecretUnlock()`. The element MUST NOT have: `title` attr, `cursor`
  change (force `cursor:'default'`), hover styling, or any aria/text hinting at a
  secret. Keep the click handler generic; do not add code comments describing the
  secret behavior.

### 3.6 Leaflet setup (in main.jsx)
```
import L from 'leaflet'
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png'
import markerIcon from 'leaflet/dist/images/marker-icon.png'
import markerShadow from 'leaflet/dist/images/marker-shadow.png'
L.Icon.Default.mergeOptions({ iconRetinaUrl: markerIcon2x, iconUrl: markerIcon, shadowUrl: markerShadow })
```
Map/feature agents should prefer `CircleMarker` / `L.divIcon` for sample dots to
avoid asset issues; the above fixes any default `<Marker>` use.

### 3.7 sessionStorage keys (shared across pages)
- `ff_secret_ok` = `'1'` after a correct secret code (gates `/login`).
- `ff_otp_pending` = `'1'` after an email is submitted on `/login` (gates `/otp`).
- `ff_login_email` = the email typed on `/login` (shown on `/otp`, and what its
  resend control asks for a new code for).
- `ff_code_requested_at` = `String(Date.now())` when a code was last requested
  (drives the 60 s resend countdown on `/otp`).
These are client-side UX gates only; the server enforces everything. Remove all
four on logout and after successful verification (then navigate to
`/dashboard`).

---

## 4. Public page (owned by agent: PUBLIC) + Auth and Admins pages (owned by agent: AUTH)

### 4.1 PUBLIC agent files
- `frontend/src/pages/PublicHome.jsx` (default export `PublicHome`)
- `frontend/src/pages/public/Hero.jsx`, `Mission.jsx`, `HowItWorks.jsx`,
  `WhereWeAre.jsx`, `Contact.jsx`, `SecretCodeBox.jsx`
- `frontend/src/pages/public/public.css`

PublicHome: a `.ff-nav` with `<Logo variant="public" onSecretUnlock={...}/>` TOP
LEFT and nothing that links to the private area. Below, sections in order: Hero,
Mission ("Our Mission" - Fort Bend County farmers along the Brazos River),
HowItWorks (3 steps: collect samples → analyze soil → get recommendations),
WhereWeAre (Leaflet map centered on Fort Bend County `[29.53,-95.77]` zoom ~10,
OSM tiles, a `Circle`/`Polygon` marking the county; wrap map in `.ff-map`),
Contact (email shown as `farmersforward2026@gmail.com` + a form: name, email, message
→ `api.sendContact`; show success/error, disable while submitting).

SecretCodeBox: rendered by PublicHome only after `onSecretUnlock` fires. A plain,
minimal, UNSTYLED text input (no label, no placeholder hint about its purpose, no
branding - a bare input, maybe fixed top-left). On Enter: `await
api.verifyCode(value)`. If `{ok:true}` → `sessionStorage.setItem('ff_secret_ok','1')`
then `navigate('/login')`. If `{ok:false}` → hide the box silently (no message).
Do not add code comments explaining what this is.

Use brand tokens/utility classes for the visible marketing sections. Hero uses
brand blue; a red accent somewhere. Everything responsive.

### 4.2 AUTH agent files
- `frontend/src/pages/Login.jsx` (default export `Login`)
- `frontend/src/pages/Otp.jsx` (default export `Otp`)
- `frontend/src/pages/auth.css`

Login: guard - on mount if `sessionStorage.getItem('ff_secret_ok') !== '1'` →
`<Navigate to="/" replace/>`. Branded card (Logo variant public but WITHOUT secret
handler is fine - just render the logo image; simplest: `<Logo variant="dashboard"/>`
is OK here OR just the img). ONE field: email (`type="email"`,
`autoComplete="email"`). Submit → `api.login(email.trim())`. On success set
`ff_otp_pending='1'`, `ff_login_email` (the trimmed email) and
`ff_code_requested_at=String(Date.now())`, then `navigate('/otp')`. The copy
must never imply that the email exists or that a code was definitely sent. On
400 show `err.payload.error` ('Please enter a valid email address'); on 403 (the
passcode gate expired server-side) remove `ff_secret_ok` and `navigate('/')`;
anything else gets a generic error. Disable button while pending; brand styling.

Otp: guard - if `sessionStorage.getItem('ff_otp_pending') !== '1'` → `<Navigate
to="/login" replace/>`. Show the `ff_login_email` address, worded conditionally
("If `<email>` is registered, we've sent a 6-digit code to it"). Six individual
single-digit input boxes (auto-advance on input, backspace to previous, paste of
6 digits fills all). A 5-minute countdown timer display measured from
`ff_code_requested_at` (client-side, informational; server enforces expiry). A
"Send a new code" control → `api.login(ff_login_email)`, disabled (showing the
countdown) until 60 s after `ff_code_requested_at`, which a resend resets; its
confirmation is conditional too ("If that email is registered, a new code is on
its way."), and a 403 removes `ff_secret_ok` and goes to `/` as on Login. A "Use
a different email" control removes `ff_otp_pending` and goes back to `/login`.
On complete/submit → `api.verifyOtp(code)`. Success → remove all four §3.7
keys, `navigate('/dashboard')`. On error: if `err.payload.error === 'locked'`
(status 423) → show "Too many attempts - please log in again", clear
`ff_otp_pending`, and after a moment `navigate('/login')`. If `invalid` → show
"Incorrect code" + attempts remaining from `err.payload.attempts_remaining`,
clear the boxes. If `expired` → show "Code expired - log in again" then go to
`/login`. Brand styling; 6 boxes centered.

### 4.3 Admins page (owner only)
- `frontend/src/pages/Admins.jsx` (default export `Admins`), routed at
  `/admins` behind `<RequireAuth ownerOnly>` (§3.2); styles (with the admin
  header's) in `frontend/src/dashboard/admin.css`, imported right after
  `dashboard.css` by Dashboard.jsx and Admins.jsx. API calls through
  `frontend/src/api/admins.js`: default export `adminsApi` with
  `listAdmins()` → `{admins, warnings}`, `createAdmin({email, display_name})`
  → `{admin}` (throws on 400/409), `setAdminActive(id, isActive)` → `{admin}`
  (throws on 400/404). Each `warnings` entry is shown as a note above the form.

`<AdminNav/>` on top. An "Add an admin" form (email + display name; client-side
checks for blanks and the 100-character display-name limit, which the server
validates again) → `adminsApi.createAdmin({email, display_name})`; on 400 or 409 show
`err.payload.error`. A table of every admin from `adminsApi.listAdmins()` (oldest
first): display name ("You" on the signed-in admin's row), email, role,
Active/Deactivated, and last login (local date and time, or "Never"). Every row
except the signed-in admin's own has Deactivate (after a confirm, since they are
signed out at once) or Reactivate → `adminsApi.setAdminActive(id, bool)`. There is no
delete control. A 403 (no longer an owner) → `/dashboard`.

---

## 5. Private dashboard (owned by agent: DASHBOARD)

Files (all default exports named as the file):
- `frontend/src/pages/Dashboard.jsx`
- `frontend/src/dashboard/DashboardProvider.jsx` (React context store, exports
  `DashboardProvider` default + `useDashboard` named hook)
- `frontend/src/dashboard/Step1FieldSetup.jsx`
- `frontend/src/dashboard/Step2DataEntry.jsx`
- `frontend/src/dashboard/Step3Review.jsx`
- `frontend/src/dashboard/Step4Results.jsx`
- `frontend/src/dashboard/geo.js` (pure functions)
- `frontend/src/dashboard/dashboard.css`

Dashboard shell: the shared `<AdminNav/>` header (§3.1): `.ff-nav` with `<Logo
variant="dashboard"/>` top-left (click → home), "Logged in as" + the admin's
`display_name`, for owners the links Field notebook (`/dashboard`) and Admins
(`/admins`), and a Logout button (`api.logout()` → remove the four §3.7 keys →
`navigate('/')`).
A `.ff-stepper` with 4 steps. Renders the current step. Wrap everything in
`<DashboardProvider>`.

Store (`useDashboard`) holds & persists to localStorage key `ff_field_draft`
(auto-save on every change, load on mount, clear after successful submit):
```
{ step:1..4, farmName, date,
  polygon: [[lat,lng],...],            // closed field boundary
  rows, cols,                          // grid inputs (1-12 each)
  grid: null | {rows, cols},           // last applied grid; grid lines draw from it
  baselinePoints: bool,                // point at each cell center (default true)
  points: [{label, lat, lng, cell:{row,col,rows,cols}}],  // sample points
  tweakMode: bool,
  mapView: null | {center:[lat,lng], zoom},  // last map view, shared by steps 1-2
  finderPins: [{id, lat, lng, title, name}], // Farm Finder search pins
  data: { [label]: {crop_type,ph,ec,nitrogen,phosphorus,potassium} },  // strings while typing
  analysis: null | analysisDict,       // set after submit
  farmId: null|int }
```
Provide setters and helpers (setPolygon, setPoints, updatePoint, setSectionData,
sectionComplete(label) -> bool (all 5 present & numeric), completeCount, goStep,
resetDraft, etc.).

geo.js pure functions (with JSDoc, no external deps):
- `pointInPolygon([lat,lng], polygon) -> bool` (ray casting).
- `polygonBounds(polygon) -> {minLat,maxLat,minLng,maxLng}`.
- `autoGrid(polygon, rows, cols) -> [{label,lat,lng,cell}]` - split bbox into
  rows×cols cells, take each cell center, keep only centers inside the polygon,
  and label the kept points sequentially `A1, A2, …` in row order (top→bottom,
  left→right). `cell` is `{row,col,rows,cols}`.
- `gridCellRing(polygon, cell) -> ring|null` - one cell clipped to the field
  (Sutherland-Hodgman); `null` when the cell has no area inside it.
- `gridCells(polygon, rows, cols) -> [{key,row,col,ring}]` - every cell with
  area inside the field (even if its center is outside), for the grid lines.
- `cellKey(row, col) -> 'row:col'` - shared key between cells and `point.cell`.
- `polygonCentroid(polygon) -> [lat,lng]`.
- `parseCoordinates(text) -> {lat,lng} | {error} | null` - accepts `29.5310, -95.7730`,
  space/semicolon separators, and `29.531° N, 95.773° W`; `null` means "not
  coordinates, treat as an address".

STEP 1 - Field Setup: Leaflet map (react-leaflet `MapContainer`) with **ESRI World
Imagery** satellite tiles:
`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
attribution `Tiles © Esri`. Center on Fort Bend `[29.53,-95.77]` zoom 13. User
clicks map to add polygon vertices (render as `Polyline`/`Polygon` + small
`CircleMarker`s); a "Close boundary" button closes it into `polygon`. After a
closed polygon:
- ONE GRID FLOW (no manual/auto modes): number inputs rows & cols (default
  3×3) + "Apply" → `grid = {rows, cols}` and, when "Add baseline points" is
  checked, `setGridPoints(autoGrid(...))` (re-applying regenerates both). `<GridLines cells={gridCells(...)} />`
  draws every cell outline (white, 1px, non-interactive) as one Leaflet
  `layerGroup` in pane `ff-grid` (z 390, under the points); each cell polygon
  is in `group.cellLayers` under `cellKey(row, col)` for per-cell styling.
- Map clicks: before the boundary closes they add corners; after, a click
  inside the polygon adds a point (`addPoint`, next free `A#`, no `cell`). A
  click that only closes a popup is ignored.
- "Add baseline points" checkbox (default on) acts at once on an applied grid:
  on → points at every cell center inside the field; off → grid lines only
  (confirms first if grid points' soil data would be lost).
- `setGridPoints(list)` replaces only the points with a `cell` and keeps the
  clicked ones, renumbering all `A1, A2, …` (grid first). A clicked point's
  data follows it to its new label; a grid point keeps data only when the same
  label is still a grid point at the same spot. With no points, "Next" stays
  disabled and says data entry needs at least one sample point.
- "Tweak mode" checkbox (disabled with no points): when on, points are
  draggable (`Marker draggable` with a `divIcon` dot); dragend → `updatePoint`.
- Clicking a point on the map opens a popup with a "Remove point" button; the
  side-panel list also has a remove (×) per point.
Right-side panel lists every point with label + lat/long (6 dp), and a "Copy" and
"Export CSV" (download `points.csv`) action. "Next" advances to step 2 (enabled
once ≥1 point exists). Show point labels on the map (permanent `Tooltip` or
`divIcon`).

Farm Finder (Step 1, above the map so it never covers map controls): a text input
+ "Find" button. Coordinates are parsed with `parseCoordinates`; anything else is
geocoded with OpenStreetMap Nominatim (`geocode.js`: `/search?format=jsonv2&limit=1`,
no key, requests spaced ≥1.1 s apart per the Nominatim usage policy, repeat queries
cached). Each search adds a red pin (`finderPins`, persisted with the draft) and
flies the map there (`flyTo` at zoom ≥17, or `flyToBounds` for a place's extent;
no animation under `prefers-reduced-motion`). Searching an already-pinned spot
reuses that pin. Pins are removable from their popup ("Remove pin") or from the
chip list under the input (× per pin, "Clear all pins"). Both maps open on the
stored `mapView` so pins sit in the same place when switching steps.

STEP 2 - Data Entry: same gridded map (satellite + polygon + point dots). Clicking
a point opens a side panel for that section: read-only label + lat/long, and
a required, searchable single-crop combobox (common suggestions plus custom text), and
inputs ph, ec, nitrogen, phosphorus, potassium. Inputs auto-save to the store
(→ localStorage) on every keystroke, so switching sections never loses data.
Dot color: `scoreColor(score)` once a point's five measurements are in (the
store debounces them to `/api/score`, which returns the same ML score step 4
shows), **gray** until then - each point colours as its own data lands, not
when the field is finished. Each point also shades its grid cell
(`gridCellRing(polygon, p.cell)`, `fillOpacity` 0.38) in a `<Pane>` below the
markers, so the field reads as a heatmap; a point added by clicking has no
cell and colours the dot only. A `<ScoreLegend showNoData />` control sits bottom-right.
A `.ff-progress` bar showing `completeCount / points.length`.

STEP 3 - Review + Submit: "Review All" table (`.ff-table`) of every section +
its 5 values (+ lat/long). "Submit to Database" button - enabled only when ALL
sections have one nonblank crop and five numeric measurements. On click: show `<Spinner/>` overlay, `api.submitField({farmName
→ farm_name, date, sections})` where sections = points mapped to
`{section_label,crop_type,latitude,longitude,ph,ec,nitrogen,phosphorus,potassium}`
(crop_type is a trimmed string of 1-100 characters; coordinates and readings are numbers).
On success store `analysis` + `farmId` in the store, clear the localStorage draft,
advance to step 4.

STEP 4 - Results: for each section (from `analysis.results`): a `.ff-card` with
the section label, a big color-coded score (`scoreColor(result.health_score)`
per §1) + category, crop viability list (`crop_suitability.viable_now`),
and recommendations (pH fix / EC fix / NPK adjustments - render each rec's title,
action, rate, cost, timing). The card carries its score colour as
`--score-color` (top edge + dot beside the label) so it matches its map marker.
A Leaflet map (satellite) overlaying the grid: colour each sample point
(CircleMarker) and its auto-grid cell by `scoreColor(result.health_score)`,
with the same `<ScoreLegend />`; if a polygon exists, also tint it. Download buttons: per section →
`<a href={api.reportSectionUrl(farmId,label)}>Download Report</a>`; and "Download
All Reports" → `api.reportAllUrl(farmId)`. (Anchor downloads carry the session
cookie; no JS fetch needed.) A "Start new field" button → `resetDraft()` → step 1.

Field-level: show `analysis.field_summary` (mean score, category, dominant
problem) at the top of step 4.

Everything brand-styled with tokens + utility classes; maps wrapped in `.ff-map`.

---

## 6. Infra + docs (owned by agent: INFRA)

Files: `nginx.conf`, `farmersforward.service`, `.env.example`, root `README.md`,
root `.gitignore`.

- `.env.example` - exactly these keys (empty values), in this order, each
  documented: `RESEND_API_KEY`, `RESEND_FROM`, the unused `SMTP_HOST`,
  `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, then `SECRET_KEY`
  (required), `ADMIN_SECRET_CODE`, commented `APP_ENV` (no effect),
  `DEFAULT_ADMIN_EMAIL`, `DEFAULT_ADMIN_NAME`, and commented optional:
  `OTP_RESEND_COOLDOWN_SECONDS`, `OTP_FAILURE_LIMIT`,
  `OTP_FAILURE_WINDOW_SECONDS`, `OTP_ISSUE_LIMIT_PER_HOUR`,
  `OTP_GLOBAL_LIMIT_PER_HOUR`, `DATABASE_URL`, `COOKIE_SECURE`,
  `ALLOWED_ORIGINS`, `FRONTEND_ORIGIN`.
- `nginx.conf` - server for `farmersforward.org` + `www`: HTTP→HTTPS redirect
  (port 80 → 443), TLS block referencing Let's Encrypt cert paths
  (`/etc/letsencrypt/live/farmersforward.org/...`), serve React build from
  `/var/www/farmersforward/frontend/dist` with SPA fallback (`try_files $uri
  /index.html`), and `location /api/ { proxy_pass http://127.0.0.1:8000; }` with
  standard proxy headers. Include an ACME `.well-known/acme-challenge` note or
  location. Comment the certbot step.
- `farmersforward.service` - systemd unit: runs gunicorn
  (`/usr/bin/python3 -m gunicorn --workers 3 --bind 127.0.0.1:8000 wsgi:app`)
  from `WorkingDirectory=/var/www/farmersforward/backend`, `Restart=always`,
  `EnvironmentFile=/var/www/farmersforward/.env`, `User=www-data`, After=network.
- root `README.md` - full deploy: prerequisites; clone; backend deps
  (`/usr/bin/python3 -m pip install --user -r backend/requirements.txt` and the
  model's `-r backend/FarmersForwardModel/requirements.txt`); create `.env` from
  `.env.example`; run backend (`python3 backend/app.py` for dev on :5000);
  frontend (`cd frontend && npm install && npm run dev` for dev :3000, `npm run
  build` for prod); Nginx setup (copy nginx.conf, symlink, `nginx -t`, reload);
  Certbot (`certbot --nginx -d farmersforward.org -d www.farmersforward.org`);
  systemd (`cp farmersforward.service /etc/systemd/system`, enable, start).
  Document the dev flow (triple-click logo, secret code, email → code printed to
  the Flask log in dev → dashboard), the startup admin report, the one-time
  admins migration with its rollback SQL, and the CLI scripts (§2.8). Mention
  brand + that soil inputs are pH/EC/N/P/K.
- root `.gitignore` - node_modules, dist, __pycache__, *.db, .env, outputs/*.pdf,
  models/*.joblib (keep), .DS_Store, .venv.

---

## 7. Hard rules (all agents)
1. Only write files in YOUR list. Never modify `frontend/package.json`,
   `vite.config.js`, `index.html`, `frontend/src/assets/logo.png`, or anything
   under `backend/FarmersForwardModel/`.
2. Match every name/path/shape above EXACTLY (imports must line up across agents).
3. All frontend API calls go through `src/api/client.js`. All internal links are
   relative. No hardcoded `http://localhost` in frontend source (dev proxy + prod
   nginx handle it) - EXCEPT Leaflet tile URLs (allowed).
4. Do not reveal the secret-access mechanism in comments, text, aria labels, test
   ids, or console logs.
5. Use React 19 + react-router-dom v7 APIs (`useNavigate`, `<Navigate>`,
   `<Routes>`). Function components + hooks only.
6. Keep components self-contained; brand everything with the tokens/classes.
7. Return a concise summary of what you created; do not run npm/pip/builds.

## 8. Per-sample crop selection

Every new submitted sample requires one `crop_type` string. Save it in
`samples.crop_type` and the stored analysis JSON; never infer a crop for old
readings. `init_db()` adds the nullable column idempotently on SQLite and
Postgres. Existing saved results remain readable.

Pass the crop into `recommender.build_recommendations(target_crop=...)`. Known
profiles use the existing crop-specific nitrogen targets and soil viability
limits. Case-insensitive matching and known aliases do not change the stored
label. Unknown crops remain valid custom input and explicitly use general
guidance; do not fabricate a profile or add a crop class to the trained model.
Show the assigned crop, its profile assessment, and recommendation notes in
results and PDF reports.
