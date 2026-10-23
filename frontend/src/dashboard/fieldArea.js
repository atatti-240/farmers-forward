/* ==========================================================================
   Farmers Forward - fieldArea.js
   Acres for each sample point: the part of the drawn field closest to it (a
   nearest-sample split of the boundary), so the samples' acres add up to the
   whole field and a point clicked onto the map gets its share too. Sent with
   each section at submit, where the fertilizer plan turns per-acre amounts
   into totals and bags. Pure helpers, no dependencies.
   ========================================================================== */

const EARTH_RADIUS_M = 6371008.8
const SQ_M_PER_ACRE = 4046.8564224

/**
 * A projection from `[lat, lng]` to local metres `[x, y]` around a reference
 * point. Equirectangular: plenty accurate across a single field.
 */
function projector(lat0, lng0) {
  const k = (Math.PI / 180) * EARTH_RADIUS_M
  const cos0 = Math.cos((lat0 * Math.PI) / 180)
  return ([lat, lng]) => [(lng - lng0) * k * cos0, (lat - lat0) * k]
}

/** Shoelace area of an `[x, y]` ring, in square metres. */
function ringArea(ring) {
  let twice = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    twice += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]
  }
  return Math.abs(twice) / 2
}

/**
 * True when two edges of the boundary cross, e.g. corners clicked out of
 * order (a bow-tie). Such a boundary has no meaningful area.
 */
function selfCrossing(ring) {
  const side = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
  const n = ring.length
  for (let i = 0; i < n; i++) {
    const a = ring[i]
    const b = ring[(i + 1) % n]
    for (let j = i + 2; j < n; j++) {
      if ((j + 1) % n === i) continue // the edge before this one shares corner a
      const c = ring[j]
      const d = ring[(j + 1) % n]
      if (side(a, b, c) * side(a, b, d) < 0 && side(c, d, a) * side(c, d, b) < 0) return true
    }
  }
  return false
}

/**
 * The part of `ring` where `a*x + b*y <= c`, by one Sutherland-Hodgman pass.
 * A concave field can come back with zero-width edges along the cut; they add
 * no area, so {@link ringArea} stays exact.
 */
function clipHalfPlane(ring, a, b, c) {
  const out = []
  for (let i = 0; i < ring.length; i++) {
    const cur = ring[i]
    const prev = ring[(i + ring.length - 1) % ring.length]
    const dCur = a * cur[0] + b * cur[1] - c
    const dPrev = a * prev[0] + b * prev[1] - c
    if (dCur <= 0 !== dPrev <= 0) {
      const t = dPrev / (dPrev - dCur)
      out.push([prev[0] + (cur[0] - prev[0]) * t, prev[1] + (cur[1] - prev[1]) * t])
    }
    if (dCur <= 0) out.push(cur)
  }
  return out
}

/**
 * Acres of the field closest to each sample point.
 *
 * @param {Array<[number, number]>} polygon - Field boundary as `[lat, lng]`.
 * @param {Array<{label:string, lat:number, lng:number}>} points - Samples.
 * @returns {Object<string, number>} Acres by point label; empty without a
 *   usable boundary (fewer than three corners, or edges that cross). Points at
 *   the same spot split their area evenly.
 */
export function sampleAcres(polygon, points) {
  const acres = {}
  if (!Array.isArray(polygon) || polygon.length < 3 || !Array.isArray(points)) return acres
  const lat0 = polygon.reduce((sum, v) => sum + v[0], 0) / polygon.length
  const lng0 = polygon.reduce((sum, v) => sum + v[1], 0) / polygon.length
  const project = projector(lat0, lng0)
  const field = polygon.map(project)
  if (selfCrossing(field)) return acres

  const sites = []
  for (const p of points) {
    if (!Number.isFinite(p.lat) || !Number.isFinite(p.lng)) continue
    const xy = project([p.lat, p.lng])
    const same = sites.find((s) => Math.hypot(s.xy[0] - xy[0], s.xy[1] - xy[1]) < 0.01)
    if (same) same.labels.push(p.label)
    else sites.push({ xy, labels: [p.label] })
  }

  for (const site of sites) {
    let cell = field
    for (const other of sites) {
      if (other === site || cell.length === 0) continue
      // Keep what is closer to this site than to the other one.
      const [sx, sy] = site.xy
      const [ox, oy] = other.xy
      cell = clipHalfPlane(cell, ox - sx, oy - sy, (ox * ox + oy * oy - sx * sx - sy * sy) / 2)
    }
    const each = cell.length >= 3 ? ringArea(cell) / SQ_M_PER_ACRE / site.labels.length : 0
    for (const label of site.labels) acres[label] = each
  }
  return acres
}
