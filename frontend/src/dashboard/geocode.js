/* ==========================================================================
   Farmers Forward - geocode.js
   Address lookup through OpenStreetMap Nominatim (no key). The public service
   allows at most one request per second, so requests are spaced out and
   repeated queries are answered from memory.
   https://operations.osmfoundation.org/policies/nominatim/
   ========================================================================== */

const ENDPOINT = 'https://nominatim.openstreetmap.org/search'
const MIN_INTERVAL_MS = 1100

const cache = new Map()
let nextSlot = 0

/**
 * Look up the best match for an address.
 *
 * @param {string} query - Free-form address or place name.
 * @param {{ signal?: AbortSignal }} [options]
 * @returns {Promise<{lat:number, lng:number, name:string, bounds:Array|null}|null>}
 *   The top match, or `null` when nothing was found.
 */
export async function geocodeAddress(query, { signal } = {}) {
  const key = query.trim().replace(/\s+/g, ' ').toLowerCase()
  if (cache.has(key)) return cache.get(key)

  const wait = nextSlot - Date.now()
  nextSlot = Math.max(nextSlot, Date.now()) + MIN_INTERVAL_MS
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait))
  signal?.throwIfAborted()

  const params = new URLSearchParams({ q: query.trim(), format: 'jsonv2', limit: '1' })
  const response = await fetch(`${ENDPOINT}?${params}`, {
    signal,
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(
      response.status === 429
        ? 'The address service is busy. Wait a few seconds and try again.'
        : `The address service returned an error (${response.status}). Try again, or enter coordinates.`
    )
  }
  const [hit] = await response.json()
  let result = null
  if (hit) {
    const lat = Number(hit.lat)
    const lng = Number(hit.lon)
    const box = Array.isArray(hit.boundingbox) ? hit.boundingbox.map(Number) : []
    result = Number.isFinite(lat) && Number.isFinite(lng)
      ? {
          lat,
          lng,
          name: hit.display_name || `${lat.toFixed(6)}, ${lng.toFixed(6)}`,
          // Nominatim's box is [south, north, west, east].
          bounds: box.length === 4 && box.every(Number.isFinite)
            ? [[box[0], box[2]], [box[1], box[3]]]
            : null,
        }
      : null
  }
  cache.set(key, result)
  return result
}
