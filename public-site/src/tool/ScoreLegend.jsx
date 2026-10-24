/* ==========================================================================
   Farmers Forward (public site) - ScoreLegend
   Soil health colour key, mounted as a real Leaflet control so it sits in the
   map's bottom-right corner above the attribution and never catches map clicks.
   ========================================================================== */

import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMap } from 'react-leaflet'
import L from 'leaflet'

import { NO_SCORE_COLOR, scoreGradientCss } from './soilColor.js'

const GRADIENT = scoreGradientCss()

/**
 * @param {{ showNoData?: boolean }} props - `showNoData` adds a swatch for
 *   points that have no score yet (the data-entry map).
 */
export default function ScoreLegend({ showNoData = false }) {
  const map = useMap()
  const [container, setContainer] = useState(null)

  useEffect(() => {
    const control = L.control({ position: 'bottomright' })
    control.onAdd = () => {
      const div = L.DomUtil.create('div', 'legend')
      L.DomEvent.disableClickPropagation(div)
      L.DomEvent.disableScrollPropagation(div)
      return div
    }
    control.addTo(map)
    setContainer(control.getContainer())
    return () => {
      control.remove()
      setContainer(null)
    }
  }, [map])

  if (!container) return null
  return createPortal(
    <>
      <p className="legend__title">Soil health</p>
      <div
        className="legend__bar"
        style={{ background: GRADIENT }}
        role="img"
        aria-label="Colour scale from red for poor soil, through yellow for moderate, to green for good"
      />
      <div className="legend__scale" aria-hidden="true">
        <span>Poor</span>
        <span>Moderate</span>
        <span>Good</span>
      </div>
      {showNoData && (
        <p className="legend__nodata">
          <span className="legend__dot" style={{ background: NO_SCORE_COLOR }} />
          No data yet
        </p>
      )}
    </>,
    container
  )
}
