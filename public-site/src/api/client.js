/* ==========================================================================
   Farmers Forward (public site) - API client for /api/public/*
   No cookies, no login. The backend runs on Render's free tier, which sleeps
   when idle and takes up to about a minute to wake, so every call:
     - flags itself as "slow" after WAKE_AFTER_MS, which the WakeNotice
       component turns into a "Waking up the soil lab…" state, and
     - retries network failures and 502/503/504 with backoff until
       GIVE_UP_AFTER_MS, instead of looking broken on the first try.
   Errors thrown carry `.status` and a human `.message`.
   ========================================================================== */

const BASE = (import.meta.env.VITE_API_URL || '/api/public').replace(/\/+$/, '')

const WAKE_AFTER_MS = 3000
const GIVE_UP_AFTER_MS = 100000
// One attempt may legitimately hang for the whole cold start.
const ATTEMPT_TIMEOUT_MS = 75000
const BACKOFF_MS = [1500, 3000, 5000, 8000, 10000]
const RETRY_STATUS = new Set([502, 503, 504])

/* --------------------------------------------------------- wake state */

let slowRequests = 0
const listeners = new Set()

function setSlow(delta) {
  slowRequests = Math.max(0, slowRequests + delta)
  for (const fn of listeners) fn(slowRequests > 0)
}

/** Subscribe to "a request is taking a while" changes. Returns unsubscribe. */
export function subscribeWaking(fn) {
  listeners.add(fn)
  fn(slowRequests > 0)
  return () => listeners.delete(fn)
}

/* ------------------------------------------------------------ request */

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(t)
      reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
  })
}

function friendlyError(status, payload) {
  const message =
    (payload && typeof payload === 'object' && payload.error) ||
    (status === 429
      ? 'Too many requests. Please wait a moment and try again.'
      : status >= 500
        ? 'The soil lab hit a snag. Please try again in a moment.'
        : `Request failed (${status}).`)
  const err = new Error(message)
  err.status = status
  err.payload = payload
  return err
}

/**
 * @param {string} path - under /api/public, e.g. '/score'
 * @param {{ method?: string, body?: any, signal?: AbortSignal,
 *   quiet?: boolean, as?: 'json'|'response' }} [opts] - `quiet` keeps this
 *   call out of the waking indicator (the background /health ping).
 */
async function request(path, { method = 'GET', body, signal, quiet = false, as = 'json' } = {}) {
  const started = Date.now()
  let slow = false
  const slowTimer = quiet ? null : setTimeout(() => { slow = true; setSlow(1) }, WAKE_AFTER_MS)
  let attempt = 0
  try {
    for (;;) {
      const controller = new AbortController()
      const onAbort = () => controller.abort()
      signal?.addEventListener('abort', onAbort, { once: true })
      const timeout = setTimeout(() => controller.abort(), ATTEMPT_TIMEOUT_MS)
      let res = null
      let networkError = null
      try {
        res = await fetch(`${BASE}${path}`, {
          method,
          credentials: 'omit',
          headers: body !== undefined
            ? { 'Content-Type': 'application/json', Accept: 'application/json' }
            : { Accept: 'application/json' },
          body: body !== undefined ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        })
      } catch (err) {
        if (signal?.aborted) throw err
        networkError = err
      } finally {
        clearTimeout(timeout)
        signal?.removeEventListener('abort', onAbort)
      }

      const retryable = networkError || (res && RETRY_STATUS.has(res.status))
      if (retryable && Date.now() - started < GIVE_UP_AFTER_MS) {
        await sleep(BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)], signal)
        attempt += 1
        continue
      }
      if (networkError) {
        const err = new Error(
          'We couldn’t reach the soil lab. Check your connection and try again.')
        err.status = 0
        throw err
      }
      if (!res.ok) {
        let payload = null
        try { payload = await res.json() } catch { /* not JSON */ }
        throw friendlyError(res.status, payload)
      }
      if (as === 'response') return res
      return await res.json()
    }
  } finally {
    if (slowTimer) clearTimeout(slowTimer)
    if (slow) setSlow(-1)
  }
}

/* ---------------------------------------------------------- endpoints */

/** Wake the server and start loading the model. Never throws. */
export function ping() {
  return request('/health', { quiet: true }).catch(() => null)
}

/** Live scores for the data-entry map. Nothing is stored. */
export function scoreSections(sections, { signal } = {}) {
  return request('/score', { method: 'POST', body: { sections }, signal })
}

/**
 * Run the full assessment. Returns `{ analysis, pdf_token,
 * pdf_token_expires_at }`; this response is the only copy of the results.
 */
export function createAssessment(payload) {
  return request('/assessments', { method: 'POST', body: payload })
}

/** Mapped soil survey data for a point; 404 until that feature is live. */
export function soilInfo(lat, lon, { signal } = {}) {
  const q = new URLSearchParams({ lat: String(lat), lon: String(lon) })
  return request(`/soil-info?${q}`, { signal, quiet: true })
}

/**
 * Download the combined PDF for a just-submitted assessment.
 * @param {string} token - the `pdf_token` from createAssessment.
 * @param {string|null} mapImage - data:image/jpeg URL of the results map.
 */
export async function downloadReport(token, mapImage) {
  const res = await request('/assessments/report', {
    method: 'POST',
    body: mapImage ? { token, map_image: mapImage } : { token },
    as: 'response',
  })
  let filename = 'farmers_forward_soil_report.pdf'
  const match = /filename="?([^";]+)"?/.exec(res.headers.get('Content-Disposition') || '')
  if (match) filename = match[1]
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10000)
}
