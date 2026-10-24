/* ==========================================================================
   Farmers Forward - API client (BUILD_CONTRACT §3.3)
   All calls go through fetch against the `/api` base path with the session
   cookie included. On a non-2xx response the thrown Error carries `.status`
   and `.payload` so callers can read `err.payload.error`,
   `err.payload.attempts_remaining`, etc. A 401 from anything outside
   `/auth/` means the session has ended, so the browser is also sent back to
   the public home page.
   ========================================================================== */

const BASE = import.meta.env.VITE_API_URL || '/api'

/**
 * The session is gone (logged out elsewhere, or this admin was deactivated),
 * so send the browser to the public home page. Never used for `/auth/`
 * endpoints (they answer 401 for a wrong code) and a no-op on the home page
 * itself, so it cannot loop.
 */
function sessionEnded() {
  if (window.location.pathname !== '/') window.location.assign('/')
}

/**
 * Core request helper.
 * @param {string} path - path under `/api` (e.g. '/auth/login').
 * @param {{ method?: string, body?: any }} [opts]
 * @returns {Promise<any>} parsed JSON payload.
 * @throws {Error} with `.status` and `.payload` set on non-2xx responses (a
 *   401 outside `/auth/` also sends the browser home first).
 */
async function request(path, { method = 'GET', body } = {}) {
  const opts = {
    method,
    credentials: 'include',
    headers: { Accept: 'application/json' },
  }

  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }

  const res = await fetch(`${BASE}${path}`, opts)

  // Parse JSON when present; tolerate empty/non-JSON bodies.
  let payload = null
  const text = await res.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!res.ok) {
    const message =
      (payload && typeof payload === 'object' && payload.error) ||
      `Request failed with status ${res.status}`
    const err = new Error(message)
    err.status = res.status
    err.payload = payload
    if (res.status === 401 && !path.startsWith('/auth/')) sessionEnded()
    throw err
  }

  return payload
}

/* ------------------------------------------------------------------ Auth */

/**
 * Verify the secret access code. Returns `{ ok }`; does NOT throw on
 * `{ ok: false }` (that is a 200 response).
 * @param {string} code
 * @returns {Promise<{ ok: boolean }>}
 */
export function verifyCode(code) {
  return request('/auth/verify-code', { method: 'POST', body: { code } })
}

/**
 * Ask for a one-time code by email (requires prior verifyCode success). Every
 * well-formed email gets the same reply, registered or not, so success never
 * means an admin exists or that a code was sent. Throws on non-2xx: 400 for a
 * malformed email, 403 when the secret gate is missing or expired.
 * @param {string} email
 * @returns {Promise<{ ok: boolean, message: string }>}
 */
export function login(email) {
  return request('/auth/login', { method: 'POST', body: { email } })
}

/**
 * Verify the one-time password. Throws with `.payload` on non-2xx so pages
 * can branch on `err.payload.error` / `err.payload.attempts_remaining`.
 * @param {string} code
 * @returns {Promise<{ ok: boolean }>}
 */
export function verifyOtp(code) {
  return request('/auth/otp', { method: 'POST', body: { code } })
}

/**
 * End the session.
 * @returns {Promise<{ ok: boolean }>}
 */
export function logout() {
  return request('/auth/logout', { method: 'POST' })
}

/**
 * Current auth status. When authed, `admin` is the signed-in profile and its
 * `role` is 'owner' or 'admin'.
 * @returns {Promise<{ authed: boolean, admin?: { id: string, email: string,
 *   display_name: string, role: string } }>}
 */
export function me() {
  return request('/auth/me')
}

/* --------------------------------------------------------------- Fields */

/**
 * Submit a field for analysis.
 * @param {{ farm_name: string, date: string, sections: Array<Object> }} field
 * @param {{ lat: number, lon: number }|null} [field.soil_point] - where the
 *   Soil Profile was looked up; the server attaches its cached profile
 *   (display only, never scored).
 * @param {number|null} [field.soil_respiration] - the field's soil respiration
 *   (24h CO2 burst) in ppm CO2-C, or null; rated for display, never scored.
 * @returns {Promise<Object>} the analysis result.
 */
export function submitField({ farm_name, date, sections, soil_point, soil_respiration }) {
  return request('/submit', {
    method: 'POST',
    body: { farm_name, date, sections, soil_point, soil_respiration },
  })
}

/**
 * Live soil health scores for the data-entry map. Nothing is stored.
 * @param {Array<{ section_label: string, ph: number, ec: number,
 *   nitrogen: number, phosphorus: number, potassium: number }>} sections
 * @returns {Promise<{ scores: Array<{ section_label: string,
 *   health_score: number, health_category: string }> }>}
 */
export function scoreSections(sections) {
  return request('/score', { method: 'POST', body: { sections } })
}

/**
 * Fetch a stored analysis by farm id.
 * @param {number} farmId
 * @returns {Promise<Object>} the analysis result.
 */
export function getResults(farmId) {
  return request(`/results/${farmId}`)
}

/**
 * Fetch submission history.
 * @returns {Promise<{ farms: Array<Object> }>}
 */
export function getHistory() {
  return request('/history')
}

/**
 * Mapped USDA soil survey data for one point (typical values for the soil
 * type there, not a measurement). Every lookup outcome is a 200 whose
 * `status` is 'ok' | 'partial' | 'no_data' | 'unavailable'; throws on 400
 * (invalid coordinates).
 * @param {number|string} lat
 * @param {number|string} lon
 * @returns {Promise<Object>} the soil profile payload.
 */
export function getSoilInfo(lat, lon) {
  return request('/soil-info?lat=' + encodeURIComponent(lat) + '&lon=' + encodeURIComponent(lon))
}

/**
 * Send a contact message. Throws on 400 (empty fields / invalid email).
 * @param {{ name: string, email: string, message: string }} msg
 * @returns {Promise<{ ok: boolean }>}
 */
export function sendContact({ name, email, message }) {
  return request('/contact', {
    method: 'POST',
    body: { name, email, message },
  })
}

/* ------------------------------------------------------------ Report URLs */

/**
 * Build the download URL for a single section's PDF report. Anchor downloads
 * carry the session cookie, so no JS fetch is needed.
 * @param {number} farmId
 * @param {string} label - section label.
 * @returns {string}
 */
export function reportSectionUrl(farmId, label) {
  return `${BASE}/report/${farmId}/section/${encodeURIComponent(label)}`
}

/**
 * Build the download URL for the combined (all sections) PDF report.
 * @param {number} farmId
 * @returns {string}
 */
export function reportAllUrl(farmId) {
  return `${BASE}/report/${farmId}/all`
}

/**
 * Download the combined PDF report, optionally with a field map image on its
 * cover page.
 *
 * The map is rasterised in the browser (see `dashboard/mapCapture.js`), so it
 * has to be POSTed rather than fetched through a plain `<a download>` link.
 * The response is saved via an object URL.
 *
 * @param {number} farmId
 * @param {string|null} [mapImage] - `data:image/jpeg;base64,...` of the map, or
 *   null/omitted for a report without one.
 * @returns {Promise<void>} Resolves once the download has been handed to the
 *   browser.
 * @throws {Error} with `.status` set when the server rejects the request (a
 *   401 also sends the browser home first, as `request` does).
 */
export async function downloadReportAll(farmId, mapImage) {
  const res = await fetch(`${BASE}/report/${farmId}/all`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mapImage ? { map_image: mapImage } : {}),
  })

  if (!res.ok) {
    if (res.status === 401) sessionEnded()
    const err = new Error(`Report download failed with status ${res.status}`)
    err.status = res.status
    throw err
  }

  // Prefer the filename the server set, so reports keep their farm-name slug.
  let filename = 'farmers_forward_report.pdf'
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = /filename="?([^";]+)"?/.exec(disposition)
  if (match) filename = match[1]

  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke on the next tick; revoking synchronously can cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 10000)
}

/* ------------------------------------------------------------------ API */

const api = {
  request,
  verifyCode,
  login,
  verifyOtp,
  logout,
  me,
  submitField,
  scoreSections,
  getResults,
  getHistory,
  sendContact,
  reportSectionUrl,
  reportAllUrl,
  downloadReportAll,
  getSoilInfo,
}

export default api
