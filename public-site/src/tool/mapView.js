/* ==========================================================================
   Farmers Forward (public site) - mapView.js
   Keeps the field map's center and zoom in the dashboard store so Step 1 and
   Step 2 (each with its own MapContainer) show the farm from the same view,
   and keeps Leaflet's idea of the container size in sync with the page.
   ========================================================================== */

import { useEffect } from 'react'
import { useMap, useMapEvents } from 'react-leaflet'

/** Reports `{ center: [lat, lng], zoom }` after every pan or zoom. */
export function TrackMapView({ onChange }) {
  const map = useMapEvents({
    moveend() {
      const c = map.getCenter()
      onChange({ center: [c.lat, c.lng], zoom: map.getZoom() })
    },
  })
  return null
}

/**
 * Calls `map.invalidateSize()` whenever the map container becomes visible or
 * changes size. Leaflet measures its container once at creation; if that
 * happens mid-layout, while hidden, or before a step's panel settles, tiles
 * render offset with grey gaps until the size is recomputed.
 */
export function InvalidateSizeOnVisible() {
  const map = useMap()
  useEffect(() => {
    const container = map.getContainer()
    let frame = 0
    const refresh = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        // Skip while hidden (display: none gives a 0×0 box); the observers
        // fire again once it is shown.
        if (container.offsetWidth > 0 && container.offsetHeight > 0) {
          map.invalidateSize()
        }
      })
    }

    refresh()
    const observers = []
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(refresh)
      ro.observe(container)
      observers.push(ro)
    }
    if (typeof IntersectionObserver !== 'undefined') {
      const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) refresh()
      })
      io.observe(container)
      observers.push(io)
    }
    window.addEventListener('resize', refresh)

    return () => {
      cancelAnimationFrame(frame)
      observers.forEach((o) => o.disconnect())
      window.removeEventListener('resize', refresh)
    }
  }, [map])
  return null
}

/**
 * Turns the map so the sampling grid sits square on screen.
 *
 * The grid's rows run `angle` degrees counter-clockwise from east, so turning
 * the map clockwise by the same amount lays them horizontal, with row 1 (A1
 * onward) along the top. Leaflet-rotate's bearing is that clockwise turn.
 *
 * @param {{ angle: number }} props - Degrees; 0 is north up.
 */
export function MapBearing({ angle }) {
  const map = useMap()
  useEffect(() => {
    if (typeof map.setBearing !== 'function') return
    map.setBearing(Number(angle) || 0)
  }, [map, angle])
  return null
}

/**
 * `map.fitBounds` for a map that may be turned.
 *
 * Leaflet-rotate fits a turned map to the latitude/longitude box around the
 * points, which for a field lying at 45° is about twice the field's size. This
 * fits the points themselves: project them, turn them as the screen shows
 * them, and size and center the view on that.
 *
 * @param {L.Map} map
 * @param {Array<[number, number]>} latlngs
 * @param {{ padding?: number, maxZoom?: number }} [opts]
 */
export function fitTurned(map, latlngs, { padding = 30, maxZoom = 18 } = {}) {
  const bearing = typeof map.getBearing === 'function' ? map.getBearing() : 0
  if (!bearing || latlngs.length < 2) {
    map.fitBounds(latlngs, { padding: [padding, padding], maxZoom })
    return
  }
  const t = (bearing * Math.PI) / 180
  const cos = Math.cos(t)
  const sin = Math.sin(t)
  const zoom = map.getZoom()
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (const ll of latlngs) {
    const p = map.project(ll, zoom)
    const x = p.x * cos - p.y * sin
    const y = p.x * sin + p.y * cos
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  const size = map.getSize()
  const scale = Math.min((size.x - 2 * padding) / Math.max(1, maxX - minX),
    (size.y - 2 * padding) / Math.max(1, maxY - minY))
  const snap = map.options.zoomSnap || 1
  let next = map.getScaleZoom(scale, zoom)
  next = Math.floor(next / snap) * snap
  next = Math.max(map.getMinZoom(), Math.min(maxZoom, map.getMaxZoom(), next))
  // Center of the turned box, turned back into map pixels.
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  const center = map.unproject([cx * cos + cy * sin, -cx * sin + cy * cos], zoom)
  map.setView(center, next, { animate: false })
}
