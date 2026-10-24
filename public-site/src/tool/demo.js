/* ==========================================================================
   Farmers Forward (public site) - demo.js
   "Try an example field" for visitors without a soil sensor: a real, plowed
   field near Needville in Fort Bend County, TX (traced on the Esri imagery the
   tool shows), and made-up example readings that vary across it so the map
   has something to show. The UI labels them as examples everywhere.
   ========================================================================== */

import { autoGrid } from './geo.js'

export const DEMO_POLYGON = [
  [29.452229, -95.902405],
  [29.448866, -95.898585],
  [29.4461, -95.902019],
  [29.449426, -95.905795],
]

export const DEMO_GRID = { rows: 5, cols: 5 }

/** Where the example field is, for the "Load the example field" band. */
export const DEMO_CAPTION = '29.4492° N · 95.9022° W · 13 sample points'

/** The store patch that loads the demo field with baseline points. */
export function demoField() {
  return {
    farmName: 'Example field, Fort Bend County',
    polygon: DEMO_POLYGON,
    grid: DEMO_GRID,
    rows: DEMO_GRID.rows,
    cols: DEMO_GRID.cols,
    baselinePoints: true,
    points: autoGrid(DEMO_POLYGON, DEMO_GRID.rows, DEMO_GRID.cols).map((p, i) => ({
      ...p,
      label: `A${i + 1}`,
    })),
  }
}

const round = (v, d) => Number(v.toFixed(d))

/**
 * Example readings for every point: smooth gradients across the field plus
 * a little deterministic wobble, so neighbouring points look related.
 * Acidic to the west, a salty low corner to the south-east, nitrogen-poor
 * to the south, better ground in the north.
 */
export function exampleReadings(points, crop = 'Corn') {
  if (points.length === 0) return {}
  const lats = points.map((p) => p.lat)
  const lngs = points.map((p) => p.lng)
  const [minLat, maxLat] = [Math.min(...lats), Math.max(...lats)]
  const [minLng, maxLng] = [Math.min(...lngs), Math.max(...lngs)]
  const span = (v, lo, hi) => (hi - lo > 1e-12 ? (v - lo) / (hi - lo) : 0.5)

  const out = {}
  points.forEach((p, i) => {
    const east = span(p.lng, minLng, maxLng)
    const north = span(p.lat, minLat, maxLat)
    const wobble = Math.sin(i * 12.9898) * 0.5 // -0.5..0.5, deterministic
    const lowCorner = Math.max(0, east - north) // south-east
    out[p.label] = {
      crop_type: crop,
      ph: String(round(5.2 + 1.8 * east + 0.2 * wobble, 1)),
      ec: String(round(0.35 + 3.4 * lowCorner ** 1.5 + 0.1 * Math.abs(wobble), 2)),
      nitrogen: String(Math.round(6 + 30 * north + 4 * wobble)),
      phosphorus: String(Math.round(12 + 40 * (0.6 * north + 0.4 * east) + 5 * wobble)),
      potassium: String(Math.round(100 + 140 * north + 15 * wobble)),
    }
  })
  return out
}
