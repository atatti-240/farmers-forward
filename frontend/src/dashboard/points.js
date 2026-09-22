/* ==========================================================================
   Farmers Forward - points.js
   Pure helpers for the sample-point list in the dashboard store. Points are
   `{ label, lat, lng, cell? }`: grid points carry the `cell` they were laid
   out in, points added by clicking the map have none.
   ========================================================================== */

/** First unused label in the `A1, A2, …` sequence. */
export function nextLabel(points) {
  const used = new Set(points.map((p) => p.label))
  let i = 1
  while (used.has(`A${i}`)) i += 1
  return `A${i}`
}

/**
 * Swap in a new set of grid points while keeping every clicked point.
 *
 * All points are renumbered `A1, A2, …`, grid points first. A clicked point's
 * measurements follow it to its new label; a grid point keeps data only if the
 * same label was already a grid point at the same spot (a regenerated grid
 * reuses A1, A2, … for different places).
 *
 * @param {Array<Object>} prevPoints - Current points.
 * @param {Object} prevData - Current `{ [label]: measurements }`.
 * @param {Array<Object>} gridPoints - New grid points (labels are replaced).
 * @returns {{ points: Array<Object>, data: Object }}
 */
export function mergeGridPoints(prevPoints, prevData, gridPoints) {
  const before = new Map(prevPoints.map((p) => [p.label, p]))
  const points = []
  const data = {}
  for (const p of gridPoints) {
    const label = `A${points.length + 1}`
    const old = before.get(label)
    if (old && old.cell && old.lat === p.lat && old.lng === p.lng && prevData[label]) {
      data[label] = prevData[label]
    }
    points.push({ ...p, label })
  }
  for (const p of prevPoints) {
    if (p.cell) continue
    const label = `A${points.length + 1}`
    if (prevData[p.label]) data[label] = prevData[p.label]
    points.push({ ...p, label })
  }
  return { points, data }
}
