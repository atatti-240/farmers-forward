/* ==========================================================================
   Farmers Forward - ScoreMarkers.jsx
   The results map's score-coloured sample points and their permanent labels.
   Labels are placed so none overlaps another label or point (labelPlacement.js)
   and re-placed on every zoom. The PDF's field map is a capture of this same
   map, so the report gets the same uncluttered labels.
   ========================================================================== */

import { useState } from 'react'
import { CircleMarker, Tooltip, useMapEvents } from 'react-leaflet'

import { placeLabels } from './labelPlacement.js'
import { scoreColor } from './soilColor.js'

export default function ScoreMarkers({ results, labelOf }) {
  const [, setZoom] = useState(null)
  // Pixel distances between points change only with the zoom.
  const map = useMapEvents({ zoomend: () => setZoom(map.getZoom()) })

  const markers = results
    .map((r) => ({ r, lat: Number(r.latitude), lng: Number(r.longitude) }))
    .filter((m) => Number.isFinite(m.lat) && Number.isFinite(m.lng))
  const placement = placeLabels(
    markers.map((m) => ({
      id: m.r.section_label,
      text: labelOf(m.r),
      point: map.latLngToContainerPoint([m.lat, m.lng]),
    }))
  )

  return markers.map(({ r, lat, lng }) => {
    const { direction, offset } = placement[r.section_label]
    return (
      <CircleMarker
        key={r.section_label}
        center={[lat, lng]}
        radius={9}
        pathOptions={{
          color: '#fff',
          weight: 2,
          fillColor: scoreColor(r.health_score),
          fillOpacity: 1,
        }}
      >
        {/* Keyed by placement: Leaflet reads direction and offset only when a
            tooltip is created. */}
        <Tooltip
          key={`${direction}:${offset.join(',')}`}
          permanent
          direction={direction}
          offset={offset}
          className="ff-label-tip"
        >
          {labelOf(r)}
        </Tooltip>
      </CircleMarker>
    )
  })
}
