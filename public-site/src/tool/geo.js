/* ==========================================================================
   Farmers Forward (public site) - geo.js (BUILD_CONTRACT §5)
   Pure geometry helpers for the private dashboard. No external dependencies.
   Coordinates are always [latitude, longitude] pairs, matching Leaflet's
   LatLng ordering. Longitude is treated as the planar x-axis and latitude as
   the planar y-axis for the point-in-polygon and centroid math.
   ========================================================================== */

/**
 * Ray-casting point-in-polygon test.
 *
 * @param {[number, number]} point - The test point as `[lat, lng]`.
 * @param {Array<[number, number]>} polygon - Ordered ring of `[lat, lng]`
 *   vertices. The ring may be open (the closing edge is implied).
 * @returns {boolean} `true` when the point lies strictly inside the polygon.
 */
export function pointInPolygon(point, polygon) {
  if (!Array.isArray(point) || !Array.isArray(polygon) || polygon.length < 3) {
    return false
  }
  const lat = point[0]
  const lng = point[1]
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const latI = polygon[i][0]
    const lngI = polygon[i][1]
    const latJ = polygon[j][0]
    const lngJ = polygon[j][1]
    const straddles = (latI > lat) !== (latJ > lat)
    if (
      straddles &&
      lng < ((lngJ - lngI) * (lat - latI)) / (latJ - latI) + lngI
    ) {
      inside = !inside
    }
  }
  return inside
}

/**
 * Axis-aligned bounding box of a polygon.
 *
 * @param {Array<[number, number]>} polygon - Ring of `[lat, lng]` vertices.
 * @returns {{minLat:number, maxLat:number, minLng:number, maxLng:number}|null}
 *   The bounds, or `null` for an empty/invalid polygon.
 */
export function polygonBounds(polygon) {
  if (!Array.isArray(polygon) || polygon.length === 0) return null
  let minLat = Infinity
  let maxLat = -Infinity
  let minLng = Infinity
  let maxLng = -Infinity
  for (const vertex of polygon) {
    const lat = vertex[0]
    const lng = vertex[1]
    if (lat < minLat) minLat = lat
    if (lat > maxLat) maxLat = lat
    if (lng < minLng) minLng = lng
    if (lng > maxLng) maxLng = lng
  }
  return { minLat, maxLat, minLng, maxLng }
}

/**
 * A flat frame for laying a grid over a field at an angle.
 *
 * Coordinates are projected around the field's bounding-box center (longitude
 * scaled by cos(latitude) so a degree east and a degree north are the same
 * length), then measured along grid axes turned `angleDeg` counter-clockwise
 * from east: `u` runs along the grid's rows (west → east when the angle is 0)
 * and `v` across them (south → north). At angle 0 the grid's bounding box is exactly the field's
 * latitude/longitude bounding box, so unrotated grids lay out as they always
 * have.
 *
 * @param {Array<[number, number]>} polygon - Field boundary, `[lat, lng]`.
 * @param {number} [angleDeg=0] - Grid angle, degrees counter-clockwise from east.
 */
function gridFrame(polygon, angleDeg = 0) {
  const b = polygonBounds(polygon)
  const lat0 = (b.minLat + b.maxLat) / 2
  const lng0 = (b.minLng + b.maxLng) / 2
  const k = Math.cos((lat0 * Math.PI) / 180) || 1
  const t = ((Number(angleDeg) || 0) * Math.PI) / 180
  const cos = Math.cos(t)
  const sin = Math.sin(t)
  const toUV = ([lat, lng]) => {
    const x = (lng - lng0) * k
    const y = lat - lat0
    return [x * cos + y * sin, -x * sin + y * cos]
  }
  const toLatLng = ([u, v]) => {
    const x = u * cos - v * sin
    const y = u * sin + v * cos
    return [lat0 + y, lng0 + x / k]
  }
  const uv = polygon.map(toUV)
  let minU = Infinity
  let maxU = -Infinity
  let minV = Infinity
  let maxV = -Infinity
  for (const [u, v] of uv) {
    if (u < minU) minU = u
    if (u > maxU) maxU = u
    if (v < minV) minV = v
    if (v > maxV) maxV = v
  }
  return { toUV, toLatLng, uv, minU, maxU, minV, maxV }
}

/**
 * The grid angle that lines a grid up with the field: the rotation (from the
 * field's own edges) whose bounding rectangle around the field is smallest.
 * Folded into -45°..45°, since a grid looks the same turned by 90°.
 *
 * @param {Array<[number, number]>} polygon - Field boundary, `[lat, lng]`.
 * @returns {number} Degrees counter-clockwise from east, to one decimal.
 */
export function fieldAngle(polygon) {
  if (!Array.isArray(polygon) || polygon.length < 3) return 0
  const flat = gridFrame(polygon, 0).uv
  let best = 0
  let bestArea = Infinity
  for (let i = 0; i < flat.length; i++) {
    const [x1, y1] = flat[i]
    const [x2, y2] = flat[(i + 1) % flat.length]
    if (x1 === x2 && y1 === y2) continue
    let deg = (Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI
    deg = ((((deg + 45) % 90) + 90) % 90) - 45
    const f = gridFrame(polygon, deg)
    const area = (f.maxU - f.minU) * (f.maxV - f.minV)
    if (area < bestArea - 1e-15) {
      bestArea = area
      best = deg
    }
  }
  return Math.round(best * 10) / 10
}

/**
 * Build an automatic sampling grid over the field.
 *
 * The field's bounding rectangle in the grid's frame (see {@link gridFrame};
 * at angle 0 that's its latitude/longitude bounding box) is split into
 * `rows` × `cols` equal cells. Each cell's center is kept only if it falls
 * inside the polygon. Kept points are read row by row (top → bottom, then
 * left → right, as the grid is drawn) and labelled sequentially
 * `A1, A2, A3, …`. Centers outside the polygon are skipped without using up a
 * label.
 *
 * Each point also carries `cell: { row, col, rows, cols, angle }` so its grid
 * cell can be redrawn later with {@link gridCellRing} (e.g. to shade it by
 * soil score).
 *
 * @param {Array<[number, number]>} polygon - Closed field boundary.
 * @param {number} rows - Number of grid rows (>= 1).
 * @param {number} cols - Number of grid columns (>= 1).
 * @param {number} [angle=0] - Grid angle, degrees counter-clockwise from east.
 * @returns {Array<{label:string, lat:number, lng:number, cell:Object}>} Sample
 *   points whose centers lie inside the polygon.
 */
export function autoGrid(polygon, rows, cols, angle = 0) {
  const r = Math.max(1, Math.floor(Number(rows)) || 0)
  const c = Math.max(1, Math.floor(Number(cols)) || 0)
  if (!Array.isArray(polygon) || polygon.length < 3) return []
  const a = Number(angle) || 0
  const f = gridFrame(polygon, a)
  const cellH = (f.maxV - f.minV) / r
  const cellW = (f.maxU - f.minU) / c

  const points = []
  for (let row = 0; row < r; row++) {
    // Row 0 is the top band, so subtract from the max v.
    const v = f.maxV - (row + 0.5) * cellH
    for (let col = 0; col < c; col++) {
      const u = f.minU + (col + 0.5) * cellW
      const [lat, lng] = f.toLatLng([u, v])
      if (pointInPolygon([lat, lng], polygon)) {
        points.push({
          label: `A${points.length + 1}`,
          lat,
          lng,
          cell: { row, col, rows: r, cols: c, angle: a },
        })
      }
    }
  }
  return points
}

/**
 * The part of the field polygon that lies inside one grid cell.
 *
 * Rebuilds the cell rectangle in the grid's frame (exactly as
 * {@link autoGrid} laid it out, at the cell's `angle`) and clips the polygon
 * to it with Sutherland-Hodgman. Clipping against a rectangle is exact for any
 * simple polygon, including concave fields.
 *
 * @param {Array<[number, number]>} polygon - Closed field boundary.
 * @param {{row:number, col:number, rows:number, cols:number, angle?:number}} cell
 *   - A point's `cell` from {@link autoGrid}.
 * @returns {Array<[number, number]>|null} The clipped ring as `[lat, lng]`
 *   pairs, or `null` for a point without a cell, a malformed cell, or a cell
 *   that only touches the field along an edge or at a corner (no area).
 */
export function gridCellRing(polygon, cell) {
  if (!cell || !Array.isArray(polygon) || polygon.length < 3) return null
  const { row, col, rows, cols } = cell
  const ints = [row, col, rows, cols]
  if (!ints.every(Number.isInteger) || rows < 1 || cols < 1) return null
  if (row < 0 || row >= rows || col < 0 || col >= cols) return null
  const f = gridFrame(polygon, Number(cell.angle) || 0)
  const cellH = (f.maxV - f.minV) / rows
  const cellW = (f.maxU - f.minU) / cols
  const north = f.maxV - row * cellH
  const south = north - cellH
  const west = f.minU + col * cellW
  const east = west + cellW

  // Each edge: index into [u, v], boundary value, and which side is kept.
  const edges = [
    [1, south, 1],
    [1, north, -1],
    [0, west, 1],
    [0, east, -1],
  ]
  let ring = f.uv
  for (const [axis, value, keep] of edges) {
    if (ring.length === 0) break
    const inside = (p) => (p[axis] - value) * keep >= 0
    const next = []
    for (let i = 0; i < ring.length; i++) {
      const cur = ring[i]
      const prev = ring[(i + ring.length - 1) % ring.length]
      const curIn = inside(cur)
      const prevIn = inside(prev)
      if (curIn !== prevIn) {
        const t = (value - prev[axis]) / (cur[axis] - prev[axis])
        next.push([
          prev[0] + (cur[0] - prev[0]) * t,
          prev[1] + (cur[1] - prev[1]) * t,
        ])
      }
      if (curIn) next.push(cur)
    }
    ring = next
  }
  if (ring.length < 3) return null
  // A cell that only meets a concave field along an edge clips to a sliver of
  // zero area; drawing it would just trace that edge.
  let twiceArea = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    twiceArea += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]
  }
  return Math.abs(twiceArea) > cellH * cellW * 1e-9 ? ring.map(f.toLatLng) : null
}

/** Key for one grid cell, shared by {@link gridCells} and each point's `cell`. */
export function cellKey(row, col) {
  return `${row}:${col}`
}

/**
 * Every cell of a `rows` × `cols` grid that overlaps the field, clipped to it.
 * Laid out exactly like {@link autoGrid}, so `cellKey(p.cell.row, p.cell.col)`
 * finds a point's cell. Unlike the points, a cell is kept even when its center
 * falls outside the field, so the grid lines cover the whole boundary.
 *
 * @param {Array<[number, number]>} polygon - Closed field boundary.
 * @param {number} rows - Number of grid rows (>= 1).
 * @param {number} cols - Number of grid columns (>= 1).
 * @param {number} [angle=0] - Grid angle, degrees counter-clockwise from east.
 * @returns {Array<{key:string, row:number, col:number,
 *   ring:Array<[number, number]>}>} Cells in row-major order.
 */
export function gridCells(polygon, rows, cols, angle = 0) {
  const r = Math.max(1, Math.floor(Number(rows)) || 0)
  const c = Math.max(1, Math.floor(Number(cols)) || 0)
  if (!Array.isArray(polygon) || polygon.length < 3) return []
  const cells = []
  for (let row = 0; row < r; row++) {
    for (let col = 0; col < c; col++) {
      const ring = gridCellRing(polygon, { row, col, rows: r, cols: c, angle })
      if (ring) cells.push({ key: cellKey(row, col), row, col, ring })
    }
  }
  return cells
}

/**
 * Centroid of a polygon.
 *
 * Uses the signed-area (shoelace) centroid formula. Degenerate rings (fewer
 * than three vertices, or zero area) fall back to the arithmetic mean of the
 * vertices so the function never returns `NaN` for a valid vertex list.
 *
 * @param {Array<[number, number]>} polygon - Ring of `[lat, lng]` vertices.
 * @returns {[number, number]|null} The centroid as `[lat, lng]`, or `null` for
 *   an empty polygon.
 */
export function polygonCentroid(polygon) {
  if (!Array.isArray(polygon) || polygon.length === 0) return null

  const average = () => {
    let sumLat = 0
    let sumLng = 0
    for (const v of polygon) {
      sumLat += v[0]
      sumLng += v[1]
    }
    return [sumLat / polygon.length, sumLng / polygon.length]
  }

  if (polygon.length < 3) return average()

  let area = 0
  let accLat = 0
  let accLng = 0
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const latI = polygon[i][0]
    const lngI = polygon[i][1]
    const latJ = polygon[j][0]
    const lngJ = polygon[j][1]
    // Cross product using lng as x and lat as y.
    const cross = lngJ * latI - lngI * latJ
    area += cross
    accLng += (lngJ + lngI) * cross
    accLat += (latJ + latI) * cross
  }
  area *= 0.5
  if (Math.abs(area) < 1e-12) return average()

  const lng = accLng / (6 * area)
  const lat = accLat / (6 * area)
  return [lat, lng]
}

const COORD_PART = String.raw`([NSEW])?\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?`
const COORD_PAIR = new RegExp(String.raw`^${COORD_PART}\s*(?:[,;]\s*|\s+)${COORD_PART}$`, 'i')

/**
 * Parse typed coordinates such as `29.5310, -95.7730`, `29.5310 -95.7730` or
 * `29.531° N, 95.773° W`. Without hemisphere letters the order is lat, lng.
 *
 * @param {string} text - Raw user input.
 * @returns {{lat:number, lng:number}|{error:string}|null} The coordinates, an
 *   error for a coordinate pair that is out of range, or `null` when the text
 *   is not shaped like coordinates (treat it as an address).
 */
export function parseCoordinates(text) {
  const match = String(text ?? '').trim().match(COORD_PAIR)
  if (!match) return null
  const parts = [match.slice(1, 4), match.slice(4, 7)].map(([pre, num, post]) => {
    if (pre && post) return null
    return { value: Number(num), hemi: (pre || post || '').toUpperCase() }
  })
  if (parts.includes(null)) return null

  let [latPart, lngPart] = parts
  if (latPart.hemi && lngPart.hemi) {
    const isLat = (h) => h === 'N' || h === 'S'
    if (isLat(latPart.hemi) === isLat(lngPart.hemi)) {
      return { error: 'Use one latitude (N/S) and one longitude (E/W).' }
    }
    if (!isLat(latPart.hemi)) [latPart, lngPart] = [lngPart, latPart]
  } else if (latPart.hemi || lngPart.hemi) {
    return { error: 'Add N/S and E/W to both numbers, or leave both off.' }
  }

  const signed = ({ value, hemi }) =>
    hemi === 'S' || hemi === 'W' ? -Math.abs(value) : value
  const lat = signed(latPart)
  const lng = signed(lngPart)
  if (Math.abs(lat) > 90) {
    return { error: 'Latitude must be between -90 and 90. Enter latitude first, e.g. 29.5310, -95.7730.' }
  }
  if (Math.abs(lng) > 180) {
    return { error: 'Longitude must be between -180 and 180.' }
  }
  return { lat, lng }
}
