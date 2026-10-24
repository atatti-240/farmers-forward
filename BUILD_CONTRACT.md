# Farmers Forward - Build Contract (single source of truth)

Every implementation agent MUST read this file fully and conform to it EXACTLY.
The whole app is built by parallel agents writing disjoint files; coherence
depends entirely on the shared names, paths, and shapes below. Do not invent
alternative names. Do not touch files outside your assigned list.

Repo root: `/Users/agasthyatatti/Desktop/FarmersForwardModel`

---

## 0. Environment (already installed - DO NOT run installers or builds)

- Backend interpreter: `/usr/bin/python3` (3.9.6). Installed: flask, flask-cors,
  bcrypt, twilio, reportlab, python-dotenv, gunicorn, pandas, numpy, scikit-learn,
  xgboost, matplotlib, joblib.
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
- `backend/auth.py` - bcrypt, OTP generate/verify, Twilio SMS, SMTP email,
  `login_required` decorator, admin seeding.
- `backend/model_runner.py` - run the soil pipeline on submitted sections.
- `backend/pdf_report.py` - ReportLab PDF generation.
- `backend/app.py` - Flask app + all endpoints + startup seeding + `main`.
- `backend/wsgi.py` - `from app import app` (gunicorn entry `wsgi:app`).
- `backend/requirements.txt` - web deps list (flask, flask-cors, bcrypt, twilio,
  reportlab, python-dotenv, gunicorn) plus a note that the model deps come from
  `FarmersForwardModel/requirements.txt`.

### 2.2 config.py
Load `.env` from repo root (`os.path.join(here, '..', '.env')`). Expose (env → default):
- `SECRET_KEY` → dev fallback `'dev-insecure-change-me'`
- `ADMIN_SECRET_CODE` → default `'202611'` (typed invisibly on the public page -
  see the secret-access flow; must match `SEQ` in PublicHome.jsx)
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` → None
- `SMTP_HOST`, `SMTP_PORT` (int, default 587), `SMTP_USER`, `SMTP_PASSWORD`,
  `SMTP_FROM` (default SMTP_USER) → None
- `DATABASE_PATH` → `backend/farmersforward.db` (absolute, computed)
- `OUTPUTS_DIR` → repo `outputs/` (absolute)
- `MODEL_DIR` → `backend/FarmersForwardModel` (absolute)
- `DEFAULT_ADMIN_USERNAME` → `'admin'`
- `DEFAULT_ADMIN_PASSWORD` → None (if None at seed time, generate a random 12-char
  and print it)
- `DEFAULT_ADMIN_PHONE` → `'+10000000000'`
- `DEFAULT_ADMIN_EMAIL` → `'admin@farmersforward.org'`
- `SESSION_COOKIE_SECURE` → bool from env `COOKIE_SECURE` (default False in dev)
- `FRONTEND_ORIGIN` → `'http://localhost:3000'`
- `OTP_TTL_SECONDS` → 300; `OTP_MAX_ATTEMPTS` → 3

### 2.3 database.py schema (create IF NOT EXISTS on init)
```
admins(   id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          phone TEXT, email TEXT,
          created_at TEXT NOT NULL )

otps(     id INTEGER PRIMARY KEY AUTOINCREMENT,
          admin_id INTEGER NOT NULL,
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

### 2.4 auth.py
- `hash_password(pw) -> str`, `check_password(pw, hash) -> bool` (bcrypt; store the
  utf-8 decoded hash string).
- `generate_otp() -> str` (6 digits, zero-padded, use `secrets`).
- `create_and_send_otp(admin_row) -> dict` : create OTP row (expiry = now +
  OTP_TTL_SECONDS), send via SMS + email, return `{'sms': bool, 'email': bool}`
  (True where actually sent). Delete/consume any prior unconsumed OTPs for that
  admin first.
- SMS via Twilio only if all TWILIO_* set; else skip. Email via SMTP only if
  SMTP_HOST+SMTP_USER+SMTP_PASSWORD set; else skip. **Dev fallback:** whenever an
  OTP is created, ALWAYS `print("[DEV] OTP for %s: %s" % (username, code),
  flush=True)` to stdout so the flow is testable without real SMS/email. (Never
  return the code in any HTTP response.)
- `verify_otp(admin_id, code) -> str`: returns one of `'ok'`, `'invalid'`,
  `'expired'`, `'locked'`. Load latest unconsumed OTP for admin. If none or
  expired → `'expired'`. If attempts already >= OTP_MAX_ATTEMPTS → `'locked'`. If
  code matches → mark consumed, return `'ok'`. Else increment attempts; if now
  >= max → `'locked'` else `'invalid'`.
- `seed_default_admin()`: if `admins` empty, create default admin from config
  (generate + PRINT password once if DEFAULT_ADMIN_PASSWORD unset). Print a clear
  one-time banner with username (and password if generated). Return nothing.
- `login_required` decorator: 401 `{'error':'auth required'}` unless
  `session.get('authed') is True` and `session.get('admin_id')`.

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
Set `app.secret_key = SECRET_KEY`, `SESSION_COOKIE_SAMESITE='Lax'`,
`SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE`. Enable CORS
(`flask_cors.CORS(app, supports_credentials=True, origins=[FRONTEND_ORIGIN])`).
Call `init_db()` and `seed_default_admin()` at import/startup. `if __name__ ==
'__main__': app.run(port=5000, debug=True)`.

| Method | Path | Auth | Request JSON | Success (200) | Errors |
|---|---|---|---|---|---|
| POST | `/api/auth/verify-code` | none | `{code}` | `{ok:true}` sets `session['secret_ok']=True` | wrong code → `{ok:false}` (still 200, no session) |
| POST | `/api/auth/login` | needs `session['secret_ok']` | `{username,password}` | `{ok:true, channels:{sms,email}}` sets `session['pending_admin_id']`, `session['otp_locked']=False` | no secret_ok → 403 `{error:'forbidden'}`; bad creds → 401 `{error:'Invalid credentials'}` |
| POST | `/api/auth/otp` | needs `session['pending_admin_id']` | `{code}` | `{ok:true}` sets `session['authed']=True`, `session['admin_id']`, clears pending | no pending → 401 `{error:'no pending login'}`; result `invalid`→401 `{error:'invalid',attempts_remaining:n}`; `expired`→401 `{error:'expired'}`; `locked`→423 `{error:'locked'}` and clear pending |
| POST | `/api/auth/logout` | none | - | `{ok:true}` clears session | - |
| GET | `/api/auth/me` | none | - | `{authed:bool, username?:str}` | - |
| POST | `/api/submit` | login_required | `{farm_name,date,sections:[{section_label,crop_type,latitude,longitude,ph,ec,nitrogen,phosphorus,potassium}]}` | the run_analysis dict (with farm_id) | 400 `{error}` if missing/empty sections |
| POST | `/api/score` | login_required | `{sections:[{section_label,ph,ec,nitrogen,phosphorus,potassium,crop_type?}]}` (<= 500, no coords; crop optional, makes the score crop-specific like /api/submit) | `{scores:[{section_label,health_score,health_category}]}` | 400 `{error}` if a measurement is missing/non-finite |
| GET | `/api/results/<int:farm_id>` | login_required | - | stored analysis dict | 404 `{error:'not found'}` |
| GET | `/api/history` | login_required | - | `{farms:[{farm_id,farm_name,date,created_at,n_sections,mean_health_score}]}` | - |
| POST | `/api/contact` | none | `{name,email,message}` | `{ok:true}` | 400 `{error}` if any empty or email invalid |
| GET | `/api/report/<int:farm_id>/section/<path:section_label>` | login_required | - | `application/pdf` (attachment) | 404 if farm/section missing |
| GET | `/api/report/<int:farm_id>/all` | login_required | - | `application/pdf` (attachment) | 404 |

`/api/submit` must: validate, insert `farms` (result_json after analysis) +
`samples` + `results` rows, run `model_runner.run_analysis(...,farm_id)`, return
the analysis. PDF endpoints load `farms.result_json`, pick the section (or all),
call pdf_report, and `send_file`/Response with
`Content-Disposition: attachment; filename=...`.

Email validity: simple regex `^[^@\s]+@[^@\s]+\.[^@\s]+$`.

---

## 3. Frontend - shared foundation (owned by agent: FOUNDATION)

### 3.1 Files owned
- `frontend/src/main.jsx` - mount, import styles + `leaflet/dist/leaflet.css`,
  apply the Leaflet default-icon fix (see 3.6), wrap `<App/>` in `<BrowserRouter>`.
- `frontend/src/App.jsx` - routes + guards (default export `App`).
- `frontend/src/styles/theme.css` - the `:root` tokens from §1.
- `frontend/src/styles/global.css` - reset + base + the utility classes in §3.4.
- `frontend/src/api/client.js` - API functions (§3.3).
- `frontend/src/components/Logo.jsx` - `Logo` (default export) per §3.5.
- `frontend/src/components/RequireAuth.jsx` - `RequireAuth` guard (default export).
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
  <Route path="*" element={<Navigate to="/" replace/>} />
</Routes>
```
Imports (EXACT default-export paths the other agents will provide):
`./pages/PublicHome.jsx`, `./pages/Login.jsx`, `./pages/Otp.jsx`,
`./pages/Dashboard.jsx`. `/login` and `/otp` do their OWN guard redirects
internally (see §4/§5) - App does not guard them; App only wraps `/dashboard`
with `RequireAuth`.

`RequireAuth`: on mount call `api.me()`; while pending render `<Spinner/>`
centered; if `authed` render children; else `<Navigate to="/login" replace/>`.

### 3.3 api/client.js (all use `fetch`, base path `/api`, `credentials:'include'`)
Provide a default export object `api` AND named exports, with these functions:
```
verifyCode(code)            -> POST /api/auth/verify-code {code}      -> {ok}
login(username, password)   -> POST /api/auth/login {username,password} -> {ok,channels}  (throws ApiError on !ok)
verifyOtp(code)             -> POST /api/auth/otp {code}              -> {ok} (throws w/ payload on !ok)
logout()                    -> POST /api/auth/logout                 -> {ok}
me()                        -> GET  /api/auth/me                      -> {authed, username?}
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
non-2xx so pages can show messages.

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
- `ff_otp_pending` = `'1'` after a successful `/login` (gates `/otp`).
Clear both on logout and after successful OTP (then navigate to `/dashboard`).

---

## 4. Public page (owned by agent: PUBLIC) + Auth pages (owned by agent: AUTH)

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
is OK here OR just the img). Fields username + password. Submit → `api.login`.
On success set `sessionStorage.setItem('ff_otp_pending','1')` and
`navigate('/otp')`. On error show "Invalid credentials" (generic; never say which
field). Show channels hint on success is not needed (we navigate away). Disable
button while pending; brand styling.

Otp: guard - if `sessionStorage.getItem('ff_otp_pending') !== '1'` → `<Navigate
to="/login" replace/>`. Six individual single-digit input boxes (auto-advance on
input, backspace to previous, paste of 6 digits fills all). A 5-minute countdown
timer display (client-side, informational; server enforces expiry). On complete/
submit → `api.verifyOtp(code)`. Success → clear `ff_secret_ok` and
`ff_otp_pending`, `navigate('/dashboard')`. On error: if `err.payload.error ===
'locked'` (status 423) → show "Too many attempts - please log in again", clear
`ff_otp_pending`, and after a moment `navigate('/login')`. If `invalid` → show
"Incorrect code" + attempts remaining from `err.payload.attempts_remaining`,
clear the boxes. If `expired` → show "Code expired - log in again" then go to
`/login`. Brand styling; 6 boxes centered.

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

Dashboard shell: `.ff-nav` with `<Logo variant="dashboard"/>` top-left (click →
home) and a Logout button (`api.logout()` → clear sessionStorage → `navigate('/')`).
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

- `.env.example` - exactly these keys (empty values), in this order, PLUS the
  extras our backend reads (documented): `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
  `TWILIO_PHONE_NUMBER`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
  `SMTP_FROM`, `SECRET_KEY`, `ADMIN_SECRET_CODE`, and commented optional:
  `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`, `DEFAULT_ADMIN_PHONE`,
  `DEFAULT_ADMIN_EMAIL`, `COOKIE_SECURE`, `FRONTEND_ORIGIN`.
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
  Document the dev flow (triple-click logo, secret code, login → OTP printed to
  the Flask log in dev → dashboard). Note default admin creds are printed once on
  first backend run. Mention brand + that soil inputs are pH/EC/N/P/K.
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

## 9. Soil profile (roadmap #9, organic matter for #8)

DISPLAY ONLY. For one point per field the app looks up mapped USDA soil-survey
data and shows it in Step 1 (compact "Soil at this spot" preview), Step 4 (a
card after the results map) and the combined PDF. Crop scoring, `run_analysis`
and the model inputs (pH/EC/N/P/K) are unchanged; there is no manual
organic-matter input. Every surface labels it as **mapped survey data for this
soil type, not a measurement of this field** (`source_note`, always shown).

Files: `backend/soil_info.py` (lookup + cache), `backend/soil_report.py` (PDF
page), `frontend/src/dashboard/soil.js` (`soilPointFor`, `soilKey`,
`useSoilInfo`), `SoilProfile.jsx`, `soil.css` (`ff-soil*`); small call sites in
`app.py`, `pdf_report.build_combined_pdf`, `client.js` (`getSoilInfo`,
`submitField` sends `soil_point`) and Steps 1, 3, 4.

Point (`soilPointFor`): polygon centroid (`polygonCentroid`, >= 3 vertices;
for a concave field whose centroid falls outside the boundary, the middle of
the widest east-west stretch inside the field at the centroid's latitude, else
the first sample point that is inside), else the LAST Farm Finder
pin (the most recently added one: re-searching an existing pin does not move
it to the end, and DashboardProvider/FarmFinder are out of scope), else the
first sample point. The lookup runs AT the point rounded to 4 decimals;
`point_key` = `"%.4f,%.4f"` (`-0.0000` → `0.0000`), so the cache key is exact.

Sources (stdlib `urllib`; every request sends `soil_info.USER_AGENT`,
`FarmersForward/1.0 (soil-profile lookup; +https://github.com/atatti-240/farmers-forward)`,
because urllib's default UA gets Cloudflare-flagged from Render):
- UC Davis SoilWeb landPKS (`GET .../api/landPKS.php?q=spn&lat=&lon=&r=1`):
  the soil series name (dominant `which=='a'` component, title-cased). It has
  NO organic-matter field, so it only names the series, plus texture/sand/
  silt/clay/CEC when SDA is down.
- USDA NRCS Soil Data Access (`POST .../Tabular/post.rest`, `SDA_SQL`):
  organic matter (`om_l/om_r/om_h`, top non-O horizon), texture, sand/silt/
  clay, CEC, drainage, hydrologic group, map unit, survey area. SDA decides
  the status, and every property comes from its one selected component.
- Both run concurrently under ONE 10 s wall-clock deadline (`TIMEOUT_SECONDS`,
  enforced by waiting on the workers; urlopen's timeout is per socket read). A
  source still running at the deadline counts as failed. `_http_json` reads
  the body in pieces and stops at its own deadline, so an abandoned worker
  does not linger on a server that trickles bytes. Each live lookup holds a
  sync gunicorn worker for up to 10 s (`--workers 3`); if that ever matters,
  add `--threads` or cap concurrent lookups.

| Method | Path | Auth | Request JSON | Success (200) | Errors |
|---|---|---|---|---|---|
| GET | `/api/soil-info?lat=&lon=` | login_required | query `lat`, `lon` | soil payload, for EVERY status incl. `unavailable` | 400 `{error}` if lat/lon missing, non-numeric, non-finite or out of range |
| POST | `/api/submit` | login_required | adds optional `soil_point:{lat,lon}` | analysis + `soil_point` (rounded) + `soil_profile` (cached payload or null) | bad `soil_point` is silently ignored, never a 400 |

Payload (same keys in every status; numbers or null):
```
{ status: "ok"|"partial"|"no_data"|"unavailable",
  reason_code: null|"no_survey_coverage"|"non_soil_map_unit"|"no_components"|
               "om_not_in_survey"|"properties_unavailable"|"unavailable",
  reason: null | one plain-English sentence,
  lat, lon, point_key, series, component_pct, component_kind,
  map_unit, map_unit_symbol, survey_area, texture, sand_pct, silt_pct, clay_pct,
  cec,                                   # meq/100 g at pH 7
  drainage, hydrologic_group,
  organic_matter_pct, organic_matter_low, organic_matter_high,  # 1 dp, HALF_UP
  layer_top_cm, layer_bottom_cm,
  sources: [...], source_note, fetched_at, cached }   # sources that contributed
```

Status (SDA `data_status` first):
- `dominant`/`fallback` → `ok` (series = SDA's component name, spelled as SDA
  has it; SoilWeb is credited in `sources` when its name matches, and a
  mismatch is logged). A real soil with no OM recorded → `ok` /
  `om_not_in_survey`, `organic_matter_pct` null.
- No-data cases: open water or a survey gap (`no_mukey`) → `no_data` /
  `no_survey_coverage`; a "Water", "Urban land" or other miscellaneous map unit
  → `no_data` / `non_soil_map_unit` (reason names the map unit); no components
  → `no_data` / `no_components`.
- SDA failed: SoilWeb has a component → `partial` / `properties_unavailable`
  (OM, drainage, hydrologic group null); SoilWeb empty → `no_data` /
  `no_survey_coverage`; both failed → `unavailable`.

Cache: table `soil_info_cache` (`id`, `point_key TEXT NOT NULL UNIQUE`,
`payload TEXT NOT NULL`, `fetched_at TEXT NOT NULL`), created lazily by
`soil_info` itself (`CREATE TABLE IF NOT EXISTS`, once per process and
database), NOT by `init_db()`. `id` is `INTEGER PRIMARY KEY` on SQLite and
`GENERATED ALWAYS AS IDENTITY` on Postgres. Upsert on `point_key`; a row is
fresh for 365 days. Only answers SDA gave definitively are cached (`ok`, and
the `no_data` cases SDA decided); `partial`, `unavailable` and SoilWeb-only
`no_data` are never cached, so the next lookup retries. Cache and network
errors are logged and swallowed: only bad coordinates raise. The write runs
on its own thread and the lookup waits for it at most 0.5 s
(`CACHE_WRITE_WAIT_SECONDS`), so SQLite's 5 s busy timeout behind another
request's write lock cannot stretch a lookup; the write still lands once the
lock is free.

Never blocks: a slow, failed or empty lookup never holds up drawing, gridding,
navigation, submission or the assessment. The UI shows the reason (Retry for
`partial`, `unavailable`, a SoilWeb-only `no_data` or a failed request); the
browser gives up on a request after 20 s so a stalled connection gets Retry
too. In Step 1 the preview sits below the grid controls, so its height
changing never moves Apply.

Submit and PDF: `/api/submit` reads the cache only (`cached_soil_info`, before
the write transaction opens). Without a valid `soil_point` the stored analysis
is unchanged. Step 4 re-fetches `/api/soil-info` when the stored profile is
missing, `partial` or `unavailable` (even when the tab already knows the
answer, showing it meanwhile), which also warms the cache.
`/api/report/<id>/all` adds a "Soil Profile" page after the field map when the
analysis has a `soil_point`, swapping a missing/`partial`/`unavailable` profile
for a cached one; with no profile at all it prints a "had not loaded" note.
PDF routes NEVER call the network. No `soil_point` (older reports) → no page
and the same page count as before. The single-section PDF is unchanged.

### 9.1 Microbial activity (roadmap #8 placeholder)

DISPLAY ONLY. One optional lab value for the whole field: soil respiration,
the 24-hour CO2 burst (`ppm CO2-C`: mg of CO2-C released per kg of dried soil
in the 24 hours after it is rewetted, e.g. a Haney or Solvita CO2-burst
result). When a value is entered, the server rates it against a cited band
table and Step 4 and the combined PDF show the rating. When none is entered,
both show exactly `Microbial activity: in-house test in development` (no
error, no zero score, no rating). Health scores, `run_analysis`, the crop
assessment and the recommendations are identical with or without a value.

Files: `backend/microbial.py` (`parse_respiration`, `rate`, `load_config`,
`range_text`), `backend/data/soil_respiration_bands.json` (the bands and their
citation), and a "Soil biology" block at the end of `soil_report.py`'s page.
Frontend: `frontend/src/dashboard/microbial.js` (`parseRespiration`,
`RESPIRATION_UNITS`, `NOT_PROVIDED_MESSAGE`, `MAX_RESPIRATION`),
`MicrobialInput.jsx` (Step 2), `MicrobialReview.jsx` (Step 3),
`MicrobialActivity.jsx` and `microbial.css` (`ff-microbial*`, Step 4). Small
call sites in `app.py`, `client.js` (`submitField` sends `soil_respiration`),
`DashboardProvider.jsx` (`respiration`, `setRespiration`), `SoilProfile.jsx`
and Steps 2, 3 and 4. Tests: `backend/tests/test_microbial.py`.

| Method | Path | Auth | Request JSON | Success (200) | Errors |
|---|---|---|---|---|---|
| POST | `/api/submit` | login_required | adds optional `soil_respiration` (number or numeric string, ppm CO2-C) | analysis + `microbial` (rating payload) when a value was given; NO `microbial` key when it is missing, `null`, `""` or blank | 400 `{error: "soil_respiration must be a number from 0 to 5000 (ppm CO2-C)"}` for anything else (non-numeric text, true/false, lists/objects, NaN/inf, < 0, > 5000), returned before any DB write |

Validation (`parse_respiration`): `None`, a missing key, `""` and whitespace
mean "not provided". Numbers and plain ASCII decimal strings (`45`, `45.5`,
`.5`, `1e2`, the same pattern as the browser's) become a float; `4_5` and
non-ASCII digits, which `float()` alone would read, do not. Everything
else raises `ValueError`, which submit turns into the 400. `MAX_RESPIRATION =
5000` is a typo guard (the guide says readings run from near 0 to about 1000),
not a soil limit. The browser's `parseRespiration` applies the same 0-5000
rule. An invalid entry gets an inline error in Step 2 and a warning line in
Step 3, and is sent as `null`, so the field is still submitted, just without
the value.

Payload (`analysis.microbial`, built by `rate()` after `run_analysis`):
```
{ status: "rated",
  value,                                 # 1 dp, ROUND_HALF_UP via Decimal(str(v))
  units: "ppm CO2-C", metric: "Soil respiration (24-hour CO2 burst)",
  band, band_index,                      # 0-based
  band_count: 8, band_min, band_max,     # band_max null for the top band
  range_text: "31-50" | "above 200",
  meaning, placeholder, confidence, basis,
  source: {title, publisher, year, url}, caveats: [...],
  bands: [{label, min, max}, ...] }      # for drawing the 8-step scale
# config missing or invalid (logged; the submission still succeeds):
{ status: "unrated", value, units: "ppm CO2-C",
  message: "Microbial activity rating is unavailable right now." }
```

Config (`backend/data/soil_respiration_bands.json`): read on first use (never
at import), validated, and cached under a lock. Validation requires non-empty
`bands`, a first `min` of 0, ascending `max`, a `null` max on the last band
only, non-empty labels, and a source title and url; a bad file raises
`ValueError`, which `rate()` logs before returning the "unrated" payload. The
cache is keyed by the file's path, mtime and size, so an edited file is
re-read without a restart. Source: Ward Laboratories, Inc. (Kearney,
Nebraska), *Haney Test Interpretation Guide v1.0* (2019), Soil Respiration
Ranking Table,
<https://www.wardlab.com/wp-content/uploads/2020/02/Haney-Rev-1.0-Interpretation-Guide-PDF-1.pdf>;
also in WardGuide (Raymond C. Ward, 2021), Table 66. Texas A&M AgriLife
Organic (2024) and the Noble Research Institute (2023) give the same end
points (below 10 very low, above 200 very high). `confidence: "medium"`,
`placeholder: false`: a published lab guideline based on the author's
observations, not field-calibrated and not specific to Texas soils. The file's
caveats ship with every rating and are printed under it:
- the middle rankings are a sliding scale that depends on soil type and
  climate; only below 10 and above 200 hold regardless;
- other published scales differ (Ward's 2023 Soil Health Assessment guide:
  Low 0-60, Marginal 60-120, Optimum above 120);
- AHDB warns that soils above pH 7.5 can give artificially low values.

| Band | ppm CO2-C | Meaning (shortened from the guide's "Implications") |
|---|---|---|
| Very Low | 0-10 | Very little potential for microbial activity; slow nutrient cycling and residue decomposition. |
| Low | 11-20 | Minimal potential for nutrient cycling. |
| Below Average | 21-30 | Some potential for nutrient cycling. |
| Slightly Below Average | 31-50 | Low to moderate potential for microbial activity. |
| Slightly Above Average | 51-70 | Moderate potential for microbial activity. |
| Above Average | 71-100 | Good potential for microbial activity. |
| High | 101-200 | High potential for microbial activity. |
| Very High | above 200 | High to very high potential for microbial activity and nitrogen mineralization. |

Band rule: a value falls in the first band whose max it does not exceed. The
published ranges are whole numbers, so a value between two of them goes to
the higher band (10.5 is Low), and anything above 200 is Very High (the table
prints ">201", the text says "above 200"). The band is found from the rounded
value that is shown, so the number and its band always agree (10.04 shows as
10.0, Very Low). `range_text` is `"min-max"`, and `"above <previous max>"` for
the top band. Change these numbers only from a cited source:
`test_microbial` pins them to the published table. If un-sourced thresholds
are ever swapped in, set `placeholder: true`. Every label then gets the
`PLACEHOLDER ` prefix, and the results page and the PDF show a prominent
"PLACEHOLDER thresholds: not from a published source" warning; their citation
line then reads "Rating bands: placeholder values, not taken from <source>."
with no link, and the guideline's caveats are left out.

Display of a value in a gap: 10.1 is Low, whose published range is 11-20, so
the results page and the PDF print its range from the band below's max
("Rating: Low (above 10 to 20 ppm, step 2 of 8)"). `range_text` itself is
unchanged.

Where it shows:
- Step 2: a "Soil biology (optional)" side-panel section once there are sample
  points. It has a "Soil respiration (24h CO₂ burst)" input with the unit
  (ppm CO₂-C) beside it, a one-line hint and an inline error for a bad value.
  It is a text input with a decimal keypad (`inputMode="decimal"`), not
  `type="number"`, which would blank text it cannot read ("45 ppm") so the
  draft and Step 3 never saw it. The text is kept as `respiration` in the
  dashboard draft, like `data` (cleared by "Start a new field"; like `data`, it
  stays in memory after a submit).
- Step 3: one line in the review card: the value, "not entered (optional)",
  or the invalid-value warning ("isn't a valid number" for text, "isn't a
  number from 0 to 5000 ppm CO₂-C" for 6000 or -5).
- Step 4: the full Soil Profile card ends with a "Soil biology" sub-block
  (`MicrobialActivity`) showing the value and units, the band, "range ppm,
  step N of 8", the meaning, an 8-step scale (aria-hidden: the text carries
  the information) captioned "Ranges in ppm CO₂-C" (two rows of four at
  560px and below), the citation with a link, "A published lab guideline, not
  calibrated for Texas soils." and the caveats. The card renders when the
  analysis has a `soil_point` OR a `microbial` rating. With a rating but no
  point it shows only the biology block and makes no survey lookup. The
  compact Step 1 preview never shows it.
- Combined PDF: the Soil Profile page ends with an h2 "Soil biology" block:
  "Microbial activity (soil respiration, 24-hour CO2 burst): 45.0 ppm CO2-C",
  "Rating: Slightly Below Average (31-50 ppm, step 4 of 8)", the meaning, a
  2-row x 8-column band scale with the matched band filled in, then the muted
  citation line and the caveats, kept together (KeepTogether) so a long
  survey moves the whole block to the next page. Without a `soil_point` the
  page has only its title and this block (no "had not loaded" note). An analysis with neither
  gets no page, so older reports keep their page count. The single-section
  PDF is unchanged.
