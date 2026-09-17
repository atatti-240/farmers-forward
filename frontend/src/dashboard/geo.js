/* ==========================================================================
   Farmers Forward - geo.js (BUILD_CONTRACT §5)
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
 * Build an automatic sampling grid over the polygon's bounding box.
 *
 * The bbox is split into `rows` × `cols` equal cells. Each cell's center is
 * kept only if it falls inside the polygon. Kept points are read row by row
 * (north→south, then west→east) and labelled sequentially `A1, A2, A3, …`,
 * matching manually placed points. Centers outside the polygon are skipped
 * without using up a label.
 *
 * @param {Array<[number, number]>} polygon - Closed field boundary.
 * @param {number} rows - Number of grid rows (>= 1).
 * @param {number} cols - Number of grid columns (>= 1).
 * @returns {Array<{label:string, lat:number, lng:number}>} Sample points whose
 *   centers lie inside the polygon.
 */
export function autoGrid(polygon, rows, cols) {
  const r = Math.max(1, Math.floor(Number(rows)) || 0)
  const c = Math.max(1, Math.floor(Number(cols)) || 0)
  const bounds = polygonBounds(polygon)
  if (!bounds || !Array.isArray(polygon) || polygon.length < 3) return []

  const latSpan = bounds.maxLat - bounds.minLat
  const lngSpan = bounds.maxLng - bounds.minLng
  const cellH = latSpan / r
  const cellW = lngSpan / c

  const points = []
  for (let row = 0; row < r; row++) {
    // Row 0 is the northern-most band, so subtract from the max latitude.
    const lat = bounds.maxLat - (row + 0.5) * cellH
    for (let col = 0; col < c; col++) {
      const lng = bounds.minLng + (col + 0.5) * cellW
      if (pointInPolygon([lat, lng], polygon)) {
        points.push({
          label: `A${points.length + 1}`,
          lat,
          lng,
        })
      }
    }
  }
  return points
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
