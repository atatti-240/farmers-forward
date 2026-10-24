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
