/* ==========================================================================
   Farmers Forward (public site) - mapCapture.js
   Turns the live results map into an image for the PDF report, in the browser.
   Rendering server-side would need a headless browser, which the Render free
   tier can't run, so the page rasterises its own DOM with html2canvas and
   posts the image up with the report request.

   Capturing the DOM (rather than redrawing the Leaflet layers, as
   leaflet-image does) is what keeps the score-coloured cells, the permanent
   `A1 · 52` tooltips and the legend control in the picture: only the tiles and
   vector layers are Leaflet's own, the rest is plain HTML.
   ========================================================================== */

// html2canvas is ~200 kB and only ever runs when someone downloads a report,
// so it is loaded on demand rather than shipped in the dashboard's bundle.

// Room for the tiles in view to arrive. Past this we capture regardless: a map
// with a few grey squares still beats blocking the download.
const TILE_WAIT_MS = 4000
const TILE_POLL_MS = 100
// Fallback for the pre-capture paint, for tabs that never fire a frame.
const PAINT_WAIT_MS = 300
// 2x for legible labels in print, capped so the base64 payload stays well
// under the API's 8 MB request limit.
const SCALE = 2
const MAX_WIDTH = 1600
// Comfortably inside the API's 8 MB MAX_CONTENT_LENGTH, leaving room for the
// base64 inflation and the JSON envelope.
const MAX_PAYLOAD_CHARS = 5 * 1024 * 1024
// High enough that the score colours and the point labels stay clean.
const JPEG_QUALITY = 0.92

/** Resolves once every `<img>` Leaflet has in the container has settled. */
function tilesSettled(container) {
  return new Promise((resolve) => {
    const start = Date.now()
    const check = () => {
      const imgs = Array.from(container.querySelectorAll('img'))
      // `complete` covers both a decoded tile and one that failed outright, so
      // a 404 tile can never hold the capture open for the full timeout.
      const pending = imgs.filter((img) => !img.complete)
      if (pending.length === 0 || Date.now() - start > TILE_WAIT_MS) {
        resolve()
        return
      }
      setTimeout(check, TILE_POLL_MS)
    }
    check()
  })
}

/**
 * Resolves after the browser has painted, so late style/layout work lands.
 *
 * Races a timer against the frame callbacks: a background tab never paints, so
 * requestAnimationFrame alone would hang the download for as long as the user
 * is looking at another tab.
 */
function nextPaint() {
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      resolve()
    }
    requestAnimationFrame(() => requestAnimationFrame(finish))
    setTimeout(finish, PAINT_WAIT_MS)
  })
}

/**
 * Make a cloned Leaflet map render the way the live one looks.
 *
 * Runs against html2canvas's throwaway copy of the DOM, so the map on screen is
 * never touched. Two things need fixing there:
 *
 *  - Leaflet positions its panes and every tile with `translate3d`, which
 *    html2canvas lays out incorrectly: the tiles, the grid cells and the pins
 *    each end up shifted by a different amount. Folding the translation into
 *    `left`/`top` (they are all absolutely positioned) puts everything back.
 *  - Leaflet fades a tile in by animating its opacity from 0 in a
 *    requestAnimationFrame loop, which doesn't run while the tab is in the
 *    background. A tile that has finished loading is meant to be fully opaque,
 *    so any still mid-fade is pinned to 1 rather than captured invisible.
 *
 * @param {HTMLElement} root - The cloned map container.
 */
function normaliseClonedMap(root) {
  const view = root.ownerDocument.defaultView
  if (!view) return

  for (const tile of root.querySelectorAll('.leaflet-tile-loaded')) {
    tile.style.opacity = '1'
  }

  const nodes = [root, ...root.querySelectorAll('*')]
  for (const node of nodes) {
    const transform = view.getComputedStyle(node).transform
    if (!transform || transform === 'none') continue
    // Only a pure translation can become left/top; anything rotated or scaled
    // is left for html2canvas to handle.
    const m = /^matrix\(1, 0, 0, 1, (-?[\d.]+), (-?[\d.]+)\)$/.exec(transform)
    if (!m) continue
    const tx = parseFloat(m[1])
    const ty = parseFloat(m[2])
    if (!tx && !ty) {
      node.style.transform = 'none'
      continue
    }
    const cs = view.getComputedStyle(node)
    node.style.left = `${(parseFloat(cs.left) || 0) + tx}px`
    node.style.top = `${(parseFloat(cs.top) || 0) + ty}px`
    node.style.transform = 'none'
  }
}

/**
 * JPEG data URL for `canvas`, redrawn smaller if the first encode is too big.
 *
 * JPEG rather than PNG because most of the picture is satellite photography,
 * which PNG stores about seven times larger (3 MB against 450 KB for a typical
 * field) for no visible gain — that weight would land in every report a farmer
 * downloads. html2canvas already paints onto a white background, so there is no
 * transparency to lose.
 *
 * A map captured on a very wide monitor can still be too big for the API's
 * 8 MB limit, so an oversized capture is halved until it fits rather than being
 * sent and bounced.
 */
function shrinkToBudget(canvas) {
  let current = canvas
  let url = current.toDataURL('image/jpeg', JPEG_QUALITY)
  let attempts = 0
  while (url.length > MAX_PAYLOAD_CHARS && attempts < 3) {
    const next = document.createElement('canvas')
    next.width = Math.max(1, Math.round(current.width / 2))
    next.height = Math.max(1, Math.round(current.height / 2))
    const ctx = next.getContext('2d')
    if (!ctx) break
    ctx.drawImage(current, 0, 0, next.width, next.height)
    current = next
    url = current.toDataURL('image/jpeg', JPEG_QUALITY)
    attempts += 1
  }
  return url
}

/**
 * Rasterise a Leaflet map container to a JPEG data URL.
 *
 * Returns `null` rather than throwing on any failure (a tainted canvas, a
 * detached node, html2canvas choking on a style): the report download falls
 * back to a PDF without the map instead of failing outright.
 *
 * @param {HTMLElement|null} container - The `.leaflet-container` element, or
 *   any wrapper around it.
 * @returns {Promise<string|null>} `data:image/jpeg;base64,...`, or `null`.
 */
export async function captureMapImage(container) {
  if (!container || !container.offsetWidth || !container.offsetHeight) return null

  try {
    const { default: html2canvas } = await import('html2canvas')

    await tilesSettled(container)
    await nextPaint()

    const scale = Math.min(SCALE, MAX_WIDTH / container.offsetWidth)
    const canvas = await html2canvas(container, {
      // The Esri tile server sends `Access-Control-Allow-Origin: *`, and the
      // TileLayer requests them with crossOrigin="anonymous", so the canvas
      // stays untainted and `toDataURL` is allowed to read it back.
      useCORS: true,
      allowTaint: false,
      backgroundColor: '#ffffff',
      scale: Math.max(1, scale),
      logging: false,
      // Zoom/attribution chrome is UI, not field data; it only adds clutter to
      // a printed report.
      ignoreElements: (el) =>
        el.classList &&
        (el.classList.contains('leaflet-control-zoom') ||
          el.classList.contains('leaflet-control-attribution')),
      onclone: (_doc, element) => normaliseClonedMap(element),
    })

    const url = shrinkToBudget(canvas)
    // A canvas that rendered nothing still yields a short valid data URL.
    return url && url.length > 1000 ? url : null
  } catch {
    return null
  }
}
