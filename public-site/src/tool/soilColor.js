/* ==========================================================================
   Farmers Forward (public site) - soilColor.js
   One colour scale for soil health scores (0-100), shared by the data-entry
   map, the results map and the results cards so a point reads the same
   everywhere. Red (poor) → amber (moderate) → green (good), blended smoothly.
   ========================================================================== */

/** Colour for a point with no score yet (matches the data-entry "pending" dot). */
export const NO_SCORE_COLOR = '#9aa3af'

// Brand red / amber / green (BUILD_CONTRACT §1), each pinned at the middle of
// its old hard bucket (<40, 40-70, >=70) so the gradient still agrees with
// the bucket a score used to fall in.
const STOPS = [
  { score: 20, hex: '#E8302A' },
  { score: 55, hex: '#F5A623' },
  { score: 85, hex: '#2E9E5B' },
]

function hexToHsl(hex) {
  const n = parseInt(hex.slice(1), 16)
  const r = ((n >> 16) & 255) / 255
  const g = ((n >> 8) & 255) / 255
  const b = (n & 255) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l * 100]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0)
  else if (max === g) h = (b - r) / d + 2
  else h = (r - g) / d + 4
  return [h * 60, s * 100, l * 100]
}

const HSL_STOPS = STOPS.map((s) => ({ score: s.score, hsl: hexToHsl(s.hex) }))

/**
 * Soil health score → CSS colour on the red → amber → green gradient.
 * Blends in HSL so amber → green passes through yellow rather than mud.
 *
 * @param {number|string|null|undefined} score - 0-100 health score.
 * @returns {string} A CSS colour, or {@link NO_SCORE_COLOR} when not a number.
 */
export function scoreColor(score) {
  if (score === null || score === undefined || score === '') return NO_SCORE_COLOR
  const s = Number(score)
  if (!Number.isFinite(s)) return NO_SCORE_COLOR

  let hsl
  if (s <= HSL_STOPS[0].score) {
    hsl = HSL_STOPS[0].hsl
  } else if (s >= HSL_STOPS[HSL_STOPS.length - 1].score) {
    hsl = HSL_STOPS[HSL_STOPS.length - 1].hsl
  } else {
    const i = HSL_STOPS.findIndex((stop) => s < stop.score)
    const a = HSL_STOPS[i - 1]
    const b = HSL_STOPS[i]
    const t = (s - a.score) / (b.score - a.score)
    hsl = a.hsl.map((v, k) => v + (b.hsl[k] - v) * t)
  }
  const [h, sat, l] = hsl
  return `hsl(${h.toFixed(1)}, ${sat.toFixed(1)}%, ${l.toFixed(1)}%)`
}

/** CSS `linear-gradient` of the full 0-100 scale, for the map legend bar. */
export function scoreGradientCss() {
  const parts = []
  for (let s = 0; s <= 100; s += 5) parts.push(`${scoreColor(s)} ${s}%`)
  return `linear-gradient(to right, ${parts.join(', ')})`
}
