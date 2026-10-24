/* ==========================================================================
   Farmers Forward - microbial.js
   The optional soil respiration lab value (24-hour CO2 burst): one number for
   the whole field, in ppm CO2-C. The server rates it against published bands
   and returns the rating with the analysis; it is display only and never
   changes the soil health scores. Mirrors backend/microbial.py's checks so the
   form can flag a bad value before it is sent.
   ========================================================================== */

export const RESPIRATION_UNITS = 'ppm CO₂-C'
export const NOT_PROVIDED_MESSAGE = 'Microbial activity: in-house test in development'
// A typo guard, not a soil limit: readings run from near 0 to about 1000.
export const MAX_RESPIRATION = 5000
export const INVALID_MESSAGE = 'Enter a number from 0 to 5000 (ppm CO₂-C), or leave it blank.'

// Plain decimal text such as "45", "45.5", ".5" or "1e3". Number() alone would
// also take "0x10" and "Infinity", which the server rejects.
export const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i

/**
 * Read the soil respiration field.
 *
 * @param {string|number|null|undefined} text - What is in the input.
 * @returns {{ value: number|null, error: string|null }} `value` is `null`
 *   when the field is blank (not provided, no error) or invalid (`error` set).
 */
export function parseRespiration(text) {
  if (text === undefined || text === null) return { value: null, error: null }
  const raw = typeof text === 'number' ? String(text) : typeof text === 'string' ? text.trim() : null
  if (raw === '') return { value: null, error: null }
  const n = raw !== null && DECIMAL.test(raw) ? Number(raw) : NaN
  if (!Number.isFinite(n) || n < 0 || n > MAX_RESPIRATION) {
    return { value: null, error: INVALID_MESSAGE }
  }
  // `-0` would otherwise print as "-0" in the review line.
  return { value: n === 0 ? 0 : n, error: null }
}
