/* ==========================================================================
   Farmers Forward - API client (BUILD_CONTRACT §3.3)
   All calls go through fetch against the `/api` base path with the session
   cookie included. On a non-2xx response the thrown Error carries `.status`
   and `.payload` so callers can read `err.payload.error`,
   `err.payload.attempts_remaining`, etc.
   ========================================================================== */

const BASE = import.meta.env.VITE_API_URL || '/api'

/**
 * Core request helper.
 * @param {string} path - path under `/api` (e.g. '/auth/login').
 * @param {{ method?: string, body?: any }} [opts]
 * @returns {Promise<any>} parsed JSON payload.
 * @throws {Error} with `.status` and `.payload` set on non-2xx responses.
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
 * Log in with username + password (requires prior verifyCode success).
 * Throws on non-2xx (e.g. bad credentials, missing secret gate).
 * @param {string} username
 * @param {string} password
 * @returns {Promise<{ ok: boolean, channels: { email: boolean } }>}
 */
export function login(username, password) {
  return request('/auth/login', {
    method: 'POST',
    body: { username, password },
  })
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
 * Current auth status.
 * @returns {Promise<{ authed: boolean, username?: string }>}
 */
export function me() {
  return request('/auth/me')
}

/* --------------------------------------------------------------- Fields */

/**
 * Submit a field for analysis.
 * @param {{ farm_name: string, date: string, sections: Array<Object> }} field
 * @returns {Promise<Object>} the analysis result.
 */
export function submitField({ farm_name, date, sections }) {
  return request('/submit', {
    method: 'POST',
    body: { farm_name, date, sections },
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
 * @throws {Error} with `.status` set when the server rejects the request.
 */
export async function downloadReportAll(farmId, mapImage) {
  const res = await fetch(`${BASE}/report/${farmId}/all`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mapImage ? { map_image: mapImage } : {}),
  })

  if (!res.ok) {
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
}

export default api
