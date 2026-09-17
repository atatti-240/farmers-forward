/* ==========================================================================
   Farmers Forward - Step 2: Data Entry (BUILD_CONTRACT §5)
   The same gridded satellite map. Selecting a point opens a side panel for its
   five soil measurements (pH, EC, N, P, K) which auto-save to the store on
   every keystroke. Point dots turn green once a section is complete.
   ========================================================================== */

import { useEffect, useRef, useState } from 'react'
import {
  MapContainer,
  TileLayer,
  Polygon,
  CircleMarker,
  Tooltip,
  useMap,
} from 'react-leaflet'

import CropPicker from '../components/CropPicker.jsx'
import { useDashboard } from './DashboardProvider.jsx'
import { InvalidateSizeOnVisible, TrackMapView } from './mapView.js'

const ESRI_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const ESRI_ATTR = 'Tiles © Esri'
const CENTER = [29.53, -95.77]
const ZOOM = 13
const GREEN = '#2E9E5B'
const GRAY = '#9aa3af'
const BLUE = '#4AAFE0'

const FIELDS = [
  { key: 'ph', label: 'pH', placeholder: 'e.g. 6.5' },
  { key: 'ec', label: 'EC (dS/m)', placeholder: 'e.g. 1.2' },
  { key: 'nitrogen', label: 'Nitrogen (ppm)', placeholder: 'e.g. 30' },
  { key: 'phosphorus', label: 'Phosphorus (ppm)', placeholder: 'e.g. 25' },
  { key: 'potassium', label: 'Potassium (ppm)', placeholder: 'e.g. 180' },
]

/** Fit the view to the polygon (or the sample points) when no view was saved. */
function FitView({ polygon, points }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    if (done.current) return
    const positions =
      polygon.length >= 2
        ? polygon
        : points.map((p) => [p.lat, p.lng])
    if (positions.length >= 2) {
      try {
        map.fitBounds(positions, { padding: [30, 30], maxZoom: 17 })
        done.current = true
      } catch {
        /* ignore */
      }
    }
  }, [map, polygon, points])
  return null
}

export default function Step2DataEntry() {
  const {
    polygon,
    points,
    data,
    setSectionData,
    sectionComplete,
    completeCount,
    total,
    mapView,
    setMapView,
    goStep,
  } = useDashboard()

  const [selected, setSelected] = useState(null)

  // Default the selection to the first point once points are available.
  useEffect(() => {
    if (!selected && points.length > 0) {
      setSelected(points[0].label)
    }
    if (selected && !points.some((p) => p.label === selected)) {
      setSelected(points[0] ? points[0].label : null)
    }
  }, [points, selected])

  const activePoint = points.find((p) => p.label === selected) || null
  const pct = total > 0 ? Math.round((completeCount / total) * 100) : 0

  return (
    <div>
      <div className="ff-step-head">
        <h1>Enter soil data</h1>
        <p>
          Select a sample point, choose its crop, then record its lab values. Entries save
          automatically. Switch between points without losing anything.
        </p>
      </div>

      <div className="ff-progress-wrap">
        <div className="ff-progress-wrap__meta">
          <span>Sections complete</span>
          <span>
            {completeCount} / {total}
          </span>
        </div>
        <div className="ff-progress" aria-hidden="true">
          <div className="ff-progress__bar" style={{ width: `${pct}%` }} />
        </div>
      </div>

      <div className="ff-split">
        {/* ------------------------------------------------------------ Map */}
        <div className="ff-map">
          <MapContainer
            center={mapView ? mapView.center : CENTER}
            zoom={mapView ? mapView.zoom : ZOOM}
            scrollWheelZoom
          >
            <TileLayer url={ESRI_URL} attribution={ESRI_ATTR} maxZoom={19} />
            <TrackMapView onChange={setMapView} />
            <InvalidateSizeOnVisible />
            {!mapView && <FitView polygon={polygon} points={points} />}

            {polygon.length >= 3 && (
              <Polygon
                positions={polygon}
                pathOptions={{
                  color: BLUE,
                  weight: 2,
                  fillColor: BLUE,
                  fillOpacity: 0.1,
                }}
              />
            )}

            {points.map((p) => {
              const complete = sectionComplete(p.label)
              const isActive = p.label === selected
              return (
                <CircleMarker
                  key={p.label}
                  center={[p.lat, p.lng]}
                  radius={isActive ? 9 : 7}
                  pathOptions={{
                    color: isActive ? '#1A1A2E' : '#fff',
                    weight: isActive ? 3 : 2,
                    fillColor: complete ? GREEN : GRAY,
                    fillOpacity: 1,
                  }}
                  eventHandlers={{ click: () => setSelected(p.label) }}
                >
                  <Tooltip permanent direction="top" className="ff-label-tip">
                    {p.label}
                  </Tooltip>
                </CircleMarker>
              )
            })}
          </MapContainer>
        </div>

        {/* ----------------------------------------------------- Side panel */}
        <div className="ff-side">
          {/* Section picker */}
          <div className="ff-side__section">
            <h3 className="ff-side__title">Sections</h3>
            {points.length === 0 ? (
              <p className="ff-empty">
                No sample points. Go back to step 1 to add some.
              </p>
            ) : (
              <ul className="ff-point-list">
                {points.map((p) => {
                  const complete = sectionComplete(p.label)
                  return (
                    <li key={p.label} style={{ display: 'block' }}>
                      <button
                        type="button"
                        className={`ff-point-item${p.label === selected ? ' ff-point-item--active' : ''}`}
                        onClick={() => setSelected(p.label)}
                      >
                        <span
                          className="ff-point-item__dot"
                          style={{ background: complete ? GREEN : GRAY }}
                        />
                        <span className="ff-point-item__label">{p.label}</span>
                        <span className="ff-point-item__coords">
                          {complete ? 'Complete' : 'Pending'}
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>

          {/* Entry form */}
          {activePoint && (
            <div className="ff-side__section">
              <div className="ff-readonly">
                <div className="ff-readonly__label">{activePoint.label}</div>
                <div className="ff-readonly__coords">
                  {activePoint.lat.toFixed(6)}, {activePoint.lng.toFixed(6)}
                </div>
              </div>

              <CropPicker
                key={activePoint.label}
                value={data[activePoint.label]?.crop_type ?? ''}
                onChange={(crop) => setSectionData(activePoint.label, 'crop_type', crop)}
              />
              <div className="ff-entry-grid">
                {FIELDS.map((f) => {
                  const value =
                    (data[activePoint.label] &&
                      data[activePoint.label][f.key]) ??
                    ''
                  return (
                    <div
                      key={f.key}
                      className={`ff-field${f.key === 'potassium' ? ' ff-field--full' : ''}`}
                    >
                      <label
                        className="ff-label"
                        htmlFor={`ff-in-${f.key}`}
                      >
                        {f.label}
                      </label>
                      <input
                        id={`ff-in-${f.key}`}
                        className="ff-input"
                        type="number"
                        step="any"
                        min="0"
                        inputMode="decimal"
                        placeholder={f.placeholder}
                        value={value}
                        onChange={(e) =>
                          setSectionData(
                            activePoint.label,
                            f.key,
                            e.target.value
                          )
                        }
                      />
                    </div>
                  )
                })}
              </div>
              <p className="ff-hint">
                {sectionComplete(activePoint.label)
                  ? 'Crop and all five values recorded for this section.'
                  : 'Choose a crop and enter all five values to complete this section.'}
              </p>
            </div>
          )}
        </div>
      </div>

      <div className="ff-actions">
        <button
          type="button"
          className="ff-btn ff-btn--secondary"
          onClick={() => goStep(1)}
        >
          Back
        </button>
        <span className="ff-spacer" />
        <button
          type="button"
          className="ff-btn ff-btn--primary"
          onClick={() => goStep(3)}
          disabled={points.length < 1}
        >
          Review &amp; submit
        </button>
      </div>
    </div>
  )
}
