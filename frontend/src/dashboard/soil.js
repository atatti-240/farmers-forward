/* ==========================================================================
   Farmers Forward - soil.js
   Which point the Soil Profile describes, and a hook that looks up mapped
   USDA soil survey data for it through GET /api/soil-info. The data is shown
   next to the assessment only; it never feeds the soil health scores.
   Answers are kept in memory per point, so moving between steps or dropping
   the same pin twice does not ask the server again.
   ========================================================================== */

import { useCallback, useEffect, useState } from 'react'

import api from '../api/client.js'
import { pointInPolygon, polygonCentroid } from './geo.js'

// point key -> payload, for definitive answers only (see isFinalProfile).
// 'partial', 'unavailable' and a SoilWeb-only 'no_data' mean a survey service
// was down, so they are asked again next time (the server does not cache them
// either).
const soilCache = new Map()
// point key -> pending request, so two components (or StrictMode's double
// effect run) share one fetch.
const inflight = new Map()

// The server gives up on the survey services after about 10 s. A request
// still pending well past that is stuck on the network (venue Wi-Fi, a
// proxy), so it is dropped and the panel offers Retry instead of spinning.
const REQUEST_TIMEOUT_MS = 20000

/** A finite coordinate within `limit` degrees, or `null`. */
function coord(value, limit) {
  const v = typeof value === 'string' && value.trim() !== '' ? Number(value) : value
  return typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= limit ? v : null
}

/** `v` to 4 decimals as text, with `-0.0000` written as `0.0000`. */
function fixed4(v) {
  const s = v.toFixed(4)
  return s === '-0.0000' ? '0.0000' : s
}

/** A checked `{ lat, lon }` rounded to 4 decimals, or `null`. */
function roundedPoint(lat, lon) {
  const la = coord(lat, 90)
  const lo = coord(lon, 180)
  if (la === null || lo === null) return null
  return { lat: Number(fixed4(la)), lon: Number(fixed4(lo)) }
}

/**
 * A point inside `polygon` on the east-west line at `lat`: the middle of the
 * widest stretch of that line inside the field, as `[lat, lng]`, or `null`.
 * Crossings follow the same rule as `pointInPolygon`.
 */
function insideOnLine(polygon, lat) {
  const crossings = []
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [latI, lngI] = polygon[i]
    const [latJ, lngJ] = polygon[j]
    if ((latI > lat) !== (latJ > lat)) {
      crossings.push(lngI + ((lat - latI) * (lngJ - lngI)) / (latJ - latI))
    }
  }
  crossings.sort((a, b) => a - b)
  let best = null
  for (let k = 0; k + 1 < crossings.length; k += 2) {
    if (!best || crossings[k + 1] - crossings[k] > best[1] - best[0]) {
      best = [crossings[k], crossings[k + 1]]
    }
  }
  return best ? [lat, (best[0] + best[1]) / 2] : null
}

/**
 * The spot inside a closed boundary to describe: its centroid, or, when a
 * concave field (an L, or a bend along a bayou) puts that outside, a point
 * inside the field level with it, else the first sample point that is
 * inside. `null` if none can be used.
 */
function fieldSpot(polygon, points) {
  const c = polygonCentroid(polygon)
  const candidates = [c, c && insideOnLine(polygon, c[0])]
  for (const pt of Array.isArray(points) ? points : []) {
    if (pt) candidates.push([pt.lat, pt.lng])
  }
  const inside = candidates.find(
    (q) => q && roundedPoint(q[0], q[1]) && pointInPolygon(q, polygon)
  )
  // Nothing inside (a sliver of a field): the centroid is still the closest
  // thing to "the field centre".
  const spot = inside || c
  return spot ? roundedPoint(spot[0], spot[1]) : null
}

/**
 * The point to look up soil survey data for: the field's centroid once a
 * boundary is closed (or a spot inside the field when the centroid is not),
 * else the latest Farm Finder pin, else the first sample point. Rounded to 4
 * decimals (about 11 m), which is the point the server queries and caches, so
 * the preview and the submitted field agree.
 *
 * @param {{ polygon?: Array<[number, number]>, finderPins?: Array<Object>,
 *   points?: Array<Object> }} field - Dashboard state; pins and points use
 *   `lat` / `lng`.
 * @returns {{ lat: number, lon: number }|null}
 */
export function soilPointFor({ polygon, finderPins, points } = {}) {
  if (Array.isArray(polygon) && polygon.length >= 3) {
    const p = fieldSpot(polygon, points)
    if (p) return p
  }
  const pins = Array.isArray(finderPins) ? finderPins : []
  for (let i = pins.length - 1; i >= 0; i--) {
    const p = pins[i] && roundedPoint(pins[i].lat, pins[i].lng)
    if (p) return p
  }
  for (const pt of Array.isArray(points) ? points : []) {
    const p = pt && roundedPoint(pt.lat, pt.lng)
    if (p) return p
  }
  return null
}

/**
 * Cache key for a point, the same `"lat,lon"` text the server uses as its
 * `point_key` (4 decimals).
 *
 * @param {{ lat: number, lon?: number, lng?: number }|null} point
 * @returns {string|null} `null` for a missing or out-of-range point.
 */
export function soilKey(point) {
  if (!point) return null
  const p = roundedPoint(point.lat, point.lon ?? point.lng)
  return p ? `${fixed4(p.lat)},${fixed4(p.lon)}` : null
}

/** Whether `profile` is a final answer that needs no second look. */
export function isFinalProfile(profile) {
  if (!profile) return false
  if (profile.status === 'ok') return true
  // 'no_data' is only final when Soil Data Access decided it; from SoilWeb
  // alone it means SDA did not answer.
  return (
    profile.status === 'no_data' &&
    Array.isArray(profile.sources) &&
    profile.sources.includes('USDA Soil Data Access')
  )
}

/** `promise`, or a rejection once `ms` have passed without an answer. */
function withTimeout(promise, ms) {
  let timer
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('The soil survey lookup timed out.')), ms)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

/**
 * One request per point at a time; definitive answers are remembered.
 * `fresh` asks the server even when this tab already has an answer.
 */
function loadSoil(key, fresh = false) {
  const cached = soilCache.get(key)
  if (cached && !fresh) return Promise.resolve(cached)
  let pending = inflight.get(key)
  if (!pending) {
    const [lat, lon] = key.split(',')
    pending = withTimeout(api.getSoilInfo(lat, lon), REQUEST_TIMEOUT_MS)
      .then((profile) => {
        if (!profile || typeof profile !== 'object' || typeof profile.status !== 'string') {
          throw new Error('Unexpected soil survey response.')
        }
        if (isFinalProfile(profile)) soilCache.set(key, profile)
        return profile
      })
      .finally(() => inflight.delete(key))
    inflight.set(key, pending)
  }
  return pending
}

/** A sentence for a failed lookup (the server itself never 500s on these). */
function errorMessage(err) {
  if (err instanceof TypeError) {
    return 'Couldn’t reach the server for soil survey data. Check your connection and try again.'
  }
  return 'The soil survey lookup didn’t work. The assessment is not affected; try again in a minute.'
}

/**
 * Soil survey data for one point, fetched in the background.
 *
 * A stored profile that is already final is shown as is. A missing, partial
 * or unavailable one is looked up again (which also warms the server cache
 * the PDF report reads). Responses for a point the caller has since moved
 * away from are ignored.
 *
 * @param {{ lat: number, lon: number }|null} point
 * @param {Object|null} [initialProfile] - A profile saved with the analysis.
 * @param {{ fromAnalysis?: boolean }} [options] - `fromAnalysis`: the point
 *   belongs to a saved analysis, so a missing or unsettled `initialProfile`
 *   means the server's cache had no answer at submit time. The server is then
 *   asked once even if this tab already knows the answer (which is shown
 *   meanwhile), since that request is what lets the PDF report include it.
 * @returns {{ status: 'idle'|'loading'|'done'|'error', profile: Object|null,
 *   error: string|null, retry: () => void }}
 */
export function useSoilInfo(point, initialProfile = null, { fromAnalysis = false } = {}) {
  const key = soilKey(point)
  const initial =
    initialProfile && typeof initialProfile === 'object' && typeof initialProfile.status === 'string'
      ? initialProfile
      : null
  const settled = isFinalProfile(initial)
  const [attempt, setAttempt] = useState(0)
  // { key, attempt, profile, error } of the last lookup that finished.
  const [result, setResult] = useState(null)

  useEffect(() => {
    if (!key || settled) return undefined
    const known = soilCache.has(key)
    if (known && !fromAnalysis) return undefined
    let active = true
    loadSoil(key, known).then(
      (profile) => {
        if (active) setResult({ key, attempt, profile, error: null })
      },
      (err) => {
        if (active) setResult({ key, attempt, profile: null, error: errorMessage(err) })
      }
    )
    return () => {
      active = false
    }
  }, [key, settled, attempt, fromAnalysis])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  if (!key || settled) {
    return { status: initial ? 'done' : 'idle', profile: initial, error: null, retry }
  }
  const cached = soilCache.get(key)
  if (cached) return { status: 'done', profile: cached, error: null, retry }
  if (result && result.key === key && result.attempt === attempt) {
    return result.error
      ? { status: 'error', profile: initial, error: result.error, retry }
      : { status: 'done', profile: result.profile, error: null, retry }
  }
  // Still looking: keep showing what we had for this point (e.g. on Retry).
  const previous = result && result.key === key ? result.profile : null
  return { status: 'loading', profile: previous || initial, error: null, retry }
}
