/* ==========================================================================
   Farmers Forward - labelPlacement.js
   Places the results map's "A1 · 52" point labels so that no label covers
   another label or another point. Works in map container pixels and has no
   dependencies, so it can be checked outside the browser.
   ========================================================================== */

// The .ff-label-tip box (dashboard.css): 11px bold text, 1px x 7px padding.
// Estimated a little generously so near misses still read as separate.
const CHAR_PX = 7
const PAD_X = 14
const HEIGHT = 19
// leaflet.css keeps each direction's tooltip 6px off its anchor.
const GAP = 6
// A point is a 9px CircleMarker with a 2px white ring.
const DOT_RADIUS = 11
const DIRECTIONS = ['top', 'bottom', 'right', 'left']
// How many label-heights a crowded label may move out before giving up.
const MAX_STEPS = 6

function labelBox(point, width, direction, shift) {
  const { x, y } = point
  if (direction === 'top') {
    return { x0: x - width / 2, x1: x + width / 2, y0: y - GAP - HEIGHT - shift, y1: y - GAP - shift }
  }
  if (direction === 'bottom') {
    return { x0: x - width / 2, x1: x + width / 2, y0: y + GAP + shift, y1: y + GAP + HEIGHT + shift }
  }
  if (direction === 'right') {
    return { x0: x + GAP + shift, x1: x + GAP + width + shift, y0: y - HEIGHT / 2, y1: y + HEIGHT / 2 }
  }
  return { x0: x - GAP - width - shift, x1: x - GAP - shift, y0: y - HEIGHT / 2, y1: y + HEIGHT / 2 }
}

function overlaps(a, b) {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1
}

function coversDot(box, p) {
  const dx = Math.max(box.x0 - p.x, 0, p.x - box.x1)
  const dy = Math.max(box.y0 - p.y, 0, p.y - box.y1)
  return dx * dx + dy * dy < DOT_RADIUS * DOT_RADIUS
}

/** Leaflet tooltip `offset` that moves a label `shift` px further out. */
function offsetFor(direction, shift) {
  if (direction === 'top') return [0, -shift]
  if (direction === 'bottom') return [0, shift]
  if (direction === 'right') return [shift, 0]
  return [-shift, 0]
}

/**
 * Choose where each label goes.
 *
 * Labels are placed top to bottom; each takes the first of above, below,
 * right, left of its point that is clear of every label placed so far and of
 * every other point, moving further out a label-height at a time when all
 * four are taken.
 *
 * @param {Array<{id:string, text:string, point:{x:number, y:number}}>} items
 *   Labels with their point's position in container pixels.
 * @returns {Object<string, {direction:string, offset:[number, number],
 *   box:{x0:number, x1:number, y0:number, y1:number}}>} Placement by id.
 */
export function placeLabels(items) {
  const placed = {}
  const boxes = []
  const order = [...items].sort((a, b) => a.point.y - b.point.y || a.point.x - b.point.x)
  for (const item of order) {
    const width = String(item.text).length * CHAR_PX + PAD_X
    let choice = null
    for (let step = 0; step < MAX_STEPS && !choice; step++) {
      const shift = step * (HEIGHT + 2)
      for (const direction of DIRECTIONS) {
        const box = labelBox(item.point, width, direction, shift)
        const clear =
          !boxes.some((b) => overlaps(b, box)) &&
          !items.some((other) => other !== item && coversDot(box, other.point))
        if (clear) {
          choice = { direction, shift, box }
          break
        }
      }
    }
    if (!choice) choice = { direction: 'top', shift: 0, box: labelBox(item.point, width, 'top', 0) }
    boxes.push(choice.box)
    placed[item.id] = {
      direction: choice.direction,
      offset: offsetFor(choice.direction, choice.shift),
      box: choice.box,
    }
  }
  return placed
}
