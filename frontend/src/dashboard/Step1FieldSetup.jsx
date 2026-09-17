/* ==========================================================================
   Farmers Forward - Step 1: Field Setup (BUILD_CONTRACT §5)
   Draw the field boundary on ESRI satellite imagery, then place sample points
   either automatically (row/col grid) or manually (click-to-drop). Points can
   be removed from the map (click → Remove) or the list, and are listed with
   6-dp coordinates that can be copied or exported as CSV.
   ========================================================================== */

import { useEffect, useRef, useState } from 'react'
import {
  MapContainer,
  TileLayer,
  Polygon,
  Polyline,
  CircleMarker,
  Marker,
  Popup,
  Tooltip,
  useMap,
  useMapEvents,
} from 'react-leaflet'
import L from 'leaflet'

import { useDashboard } from './DashboardProvider.jsx'
import FarmFinder, { FarmFinderPins } from './FarmFinder.jsx'
import { autoGrid, pointInPolygon } from './geo.js'
import { InvalidateSizeOnVisible, TrackMapView } from './mapView.js'

const ESRI_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const ESRI_ATTR = 'Tiles © Esri'
const CENTER = [29.53, -95.77]
const ZOOM = 13
const BLUE = '#4AAFE0'

/** Build a leaflet divIcon for a draggable sample point. */
function dotIcon(label) {
  return L.divIcon({
    className: 'ff-pt-divicon',
    html: `<span class="ff-pt-dot" style="background:${BLUE}"></span><span class="ff-pt-label">${label}</span>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  })
}

/**
 * Forwards map clicks to a handler that always sees fresh props. A click that
 * only dismisses an open popup is swallowed, so clicking away from a point's
 * popup doesn't also drop a new point or boundary corner.
 */
function MapClicks({ onClick }) {
  const dismissing = useRef(false)
  useMapEvents({
    // Leaflet closes popups on `preclick`, just before `click` fires.
    popupclose() {
      dismissing.current = true
      setTimeout(() => {
        dismissing.current = false
      }, 0)
    },
    click(e) {
      if (dismissing.current) return
      onClick(e.latlng.lat, e.latlng.lng)
    },
  })
  return null
}

/** Popup for a sample point on the map: label, coordinates and Remove. */
function PointPopup({ point, onRemove }) {
  return (
    <Popup className="ff-finder-popup">
      <div className="ff-finder-popup__title">{point.label}</div>
      <p className="ff-finder-popup__coords">
        {point.lat.toFixed(6)}, {point.lng.toFixed(6)}
      </p>
      <button
        type="button"
        className="ff-btn ff-btn--ghost ff-btn--sm ff-finder-popup__remove"
        onClick={(event) => {
          // Same as Farm Finder pins: keep this click from reaching the map
          // once React has torn the popup down.
          event.stopPropagation()
          onRemove(point.label)
        }}
      >
        &times; Remove point
      </button>
    </Popup>
  )
}

/**
 * Fit the view to the polygon when it becomes a closed ring. With a saved view,
 * an already-closed polygon counts as seen so the saved view is kept.
 */
function FitPolygon({ polygon, hasSavedView }) {
  const map = useMap()
  const prevLen = useRef(hasSavedView ? polygon.length : 0)
  useEffect(() => {
    const n = polygon.length
    if (n >= 3 && prevLen.current < 3) {
      try {
        map.fitBounds(polygon, { padding: [30, 30], maxZoom: 17 })
      } catch {
        /* ignore */
      }
    }
    prevLen.current = n
  }, [map, polygon])
  return null
}

function clampInt(value) {
  const v = parseInt(value, 10)
  if (Number.isNaN(v)) return ''
  return Math.min(12, Math.max(1, v))
}

export default function Step1FieldSetup() {
  const {
    farmName,
    setFarmName,
    date,
    setDate,
    polygon,
    setPolygon,
    gridMode,
    setGridMode,
    rows,
    setRows,
    cols,
    setCols,
    points,
    setPoints,
    addPoint,
    updatePoint,
    removePoint,
    clearPoints,
    tweakMode,
    setTweakMode,
    mapView,
    setMapView,
    goStep,
  } = useDashboard()

  const [map, setMap] = useState(null)
  const [verts, setVerts] = useState([])
  const [copied, setCopied] = useState(false)

  const closed = polygon.length >= 3

  function handleMapClick(lat, lng) {
    if (!closed) {
      setVerts((prev) => [...prev, [lat, lng]])
      return
    }
    if (gridMode === 'manual' && pointInPolygon([lat, lng], polygon)) {
      addPoint(lat, lng)
    }
  }

  function closeBoundary() {
    if (verts.length >= 3) {
      setPolygon(verts)
      setVerts([])
    }
  }

  function resetBoundary() {
    setPolygon([])
    clearPoints()
    setVerts([])
  }

  function generate() {
    setPoints(autoGrid(polygon, rows || 3, cols || 3))
  }

  async function copyPoints() {
    const text = points
      .map((p) => `${p.label}\t${p.lat.toFixed(6)}\t${p.lng.toFixed(6)}`)
      .join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }

  function exportCsv() {
    const lines = [['label', 'latitude', 'longitude'].join(',')]
    for (const p of points) {
      lines.push([p.label, p.lat.toFixed(6), p.lng.toFixed(6)].join(','))
    }
    const blob = new Blob([lines.join('\n')], {
      type: 'text/csv;charset=utf-8;',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'points.csv'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      <div className="ff-step-head">
        <h1>Set up your field</h1>
        <p>
          Find your farm by address or coordinates, click the satellite map to
          trace your field boundary, close it, then place soil-sample points
          automatically or by hand.
        </p>
      </div>

      <div className="ff-split">
        {/* ------------------------------------------------------------ Map */}
        <div className="ff-map-col">
          <FarmFinder map={map} />
          <div className="ff-map">
            <MapContainer
              ref={setMap}
              center={mapView ? mapView.center : CENTER}
              zoom={mapView ? mapView.zoom : ZOOM}
              scrollWheelZoom
            >
              <TileLayer url={ESRI_URL} attribution={ESRI_ATTR} maxZoom={19} />
              <TrackMapView onChange={setMapView} />
              <InvalidateSizeOnVisible />
              <MapClicks onClick={handleMapClick} />
              <FitPolygon polygon={polygon} hasSavedView={!!mapView} />
              <FarmFinderPins />

              {/* Boundary while drawing */}
              {!closed && verts.length > 0 && (
                <>
                  <Polyline
                    positions={verts}
                    pathOptions={{ color: BLUE, weight: 2, dashArray: '5,6' }}
                  />
                  {verts.map((v, i) => (
                    <CircleMarker
                      key={`v${i}`}
                      center={v}
                      radius={5}
                      pathOptions={{
                        color: '#fff',
                        weight: 2,
                        fillColor: BLUE,
                        fillOpacity: 1,
                      }}
                    />
                  ))}
                </>
              )}

              {/* Closed boundary */}
              {closed && (
                <Polygon
                  positions={polygon}
                  pathOptions={{
                    color: BLUE,
                    weight: 2,
                    fillColor: BLUE,
                    fillOpacity: 0.12,
                  }}
                />
              )}

              {/* Sample points */}
              {closed &&
                points.map((p) => {
                  const draggable =
                    gridMode === 'manual' ||
                    (gridMode === 'auto' && tweakMode)
                  if (draggable) {
                    return (
                      <Marker
                        key={p.label}
                        position={[p.lat, p.lng]}
                        draggable
                        icon={dotIcon(p.label)}
                        eventHandlers={{
                          dragend: (e) => {
                            const ll = e.target.getLatLng()
                            updatePoint(p.label, ll.lat, ll.lng)
                          },
                        }}
                      >
                        <PointPopup point={p} onRemove={removePoint} />
                      </Marker>
                    )
                  }
                  return (
                    <CircleMarker
                      key={p.label}
                      center={[p.lat, p.lng]}
                      radius={7}
                      bubblingMouseEvents={false}
                      pathOptions={{
                        color: '#fff',
                        weight: 2,
                        fillColor: BLUE,
                        fillOpacity: 1,
                      }}
                    >
                      <Tooltip permanent direction="top" className="ff-label-tip">
                        {p.label}
                      </Tooltip>
                      <PointPopup point={p} onRemove={removePoint} />
                    </CircleMarker>
                  )
                })}
            </MapContainer>
          </div>
        </div>

        {/* ----------------------------------------------------- Side panel */}
        <div className="ff-side">
          {/* Farm details */}
          <div className="ff-side__section">
            <div className="ff-field">
              <label className="ff-label" htmlFor="ff-farm-name">
                Farm / field name
              </label>
              <input
                id="ff-farm-name"
                className="ff-input"
                type="text"
                value={farmName}
                placeholder="e.g. Brazos Bottom North"
                onChange={(e) => setFarmName(e.target.value)}
              />
            </div>
            <div className="ff-field ff-mb-0">
              <label className="ff-label" htmlFor="ff-farm-date">
                Sampling date
              </label>
              <input
                id="ff-farm-date"
                className="ff-input"
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
          </div>

          {/* Boundary */}
          <div className="ff-side__section">
            <h3 className="ff-side__title">Field boundary</h3>
            {!closed ? (
              <>
                <p className="ff-hint ff-mt-0">
                  {verts.length === 0
                    ? 'Click the map to drop boundary corners.'
                    : `${verts.length} corner${verts.length === 1 ? '' : 's'} placed. Add at least 3, then close the boundary.`}
                </p>
                <div className="ff-inline">
                  <button
                    type="button"
                    className="ff-btn ff-btn--primary ff-btn--sm"
                    onClick={closeBoundary}
                    disabled={verts.length < 3}
                  >
                    Close boundary
                  </button>
                  <button
                    type="button"
                    className="ff-btn ff-btn--ghost ff-btn--sm"
                    onClick={() => setVerts((prev) => prev.slice(0, -1))}
                    disabled={verts.length === 0}
                  >
                    Undo corner
                  </button>
                  <button
                    type="button"
                    className="ff-btn ff-btn--ghost ff-btn--sm"
                    onClick={() => setVerts([])}
                    disabled={verts.length === 0}
                  >
                    Clear
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="ff-hint ff-mt-0">
                  Boundary closed ({polygon.length} corners).
                </p>
                <button
                  type="button"
                  className="ff-btn ff-btn--secondary ff-btn--sm"
                  onClick={resetBoundary}
                >
                  Redraw boundary
                </button>
              </>
            )}
          </div>

          {/* Grid controls */}
          {closed && (
            <div className="ff-side__section">
              <h3 className="ff-side__title">Sample points</h3>
              <div className="ff-toolbar">
                <div className="ff-seg" role="group" aria-label="Grid mode">
                  <button
                    type="button"
                    className={`ff-seg__btn${gridMode === 'auto' ? ' ff-seg__btn--active' : ''}`}
                    onClick={() => setGridMode('auto')}
                  >
                    Auto grid
                  </button>
                  <button
                    type="button"
                    className={`ff-seg__btn${gridMode === 'manual' ? ' ff-seg__btn--active' : ''}`}
                    onClick={() => setGridMode('manual')}
                  >
                    Manual
                  </button>
                </div>
              </div>

              {gridMode === 'auto' ? (
                <>
                  <div className="ff-num-group">
                    <div className="ff-field">
                      <label className="ff-label" htmlFor="ff-rows">
                        Rows
                      </label>
                      <input
                        id="ff-rows"
                        className="ff-input ff-num"
                        type="number"
                        min="1"
                        max="12"
                        value={rows}
                        onChange={(e) => setRows(clampInt(e.target.value))}
                      />
                    </div>
                    <div className="ff-field">
                      <label className="ff-label" htmlFor="ff-cols">
                        Columns
                      </label>
                      <input
                        id="ff-cols"
                        className="ff-input ff-num"
                        type="number"
                        min="1"
                        max="12"
                        value={cols}
                        onChange={(e) => setCols(clampInt(e.target.value))}
                      />
                    </div>
                  </div>
                  <div className="ff-inline" style={{ marginTop: 12 }}>
                    <button
                      type="button"
                      className="ff-btn ff-btn--primary ff-btn--sm"
                      onClick={generate}
                    >
                      Apply
                    </button>
                    <label className="ff-check">
                      <input
                        type="checkbox"
                        checked={tweakMode}
                        onChange={(e) => setTweakMode(e.target.checked)}
                      />
                      Tweak mode
                    </label>
                  </div>
                  <p className="ff-hint">
                    Apply spreads rows × columns points evenly across the field and
                    keeps only those inside your boundary. Change the numbers and
                    apply again to regenerate. Click a point to remove it, or turn
                    on tweak mode to drag points into place.
                  </p>
                </>
              ) : (
                <p className="ff-hint ff-mt-0">
                  Click anywhere inside the boundary to drop a labelled point.
                  Drag any point to reposition it, or click it to remove it.
                </p>
              )}
            </div>
          )}

          {/* Point list */}
          {closed && (
            <div className="ff-side__section">
              <div className="ff-inline ff-inline--between">
                <span className="ff-toolbar__label">
                  {points.length} point{points.length === 1 ? '' : 's'}
                </span>
                {points.length > 0 && (
                  <div className="ff-inline">
                    <button
                      type="button"
                      className="ff-btn ff-btn--ghost ff-btn--sm"
                      onClick={copyPoints}
                    >
                      {copied ? 'Copied!' : 'Copy'}
                    </button>
                    <button
                      type="button"
                      className="ff-btn ff-btn--ghost ff-btn--sm"
                      onClick={exportCsv}
                    >
                      Export CSV
                    </button>
                  </div>
                )}
              </div>

              {points.length === 0 ? (
                <p className="ff-empty" style={{ marginTop: 12 }}>
                  No sample points yet.
                </p>
              ) : (
                <ul className="ff-point-list" style={{ marginTop: 12 }}>
                  {points.map((p) => (
                    <li key={p.label} style={{ display: 'block' }}>
                      <div className="ff-point-item" style={{ cursor: 'default' }}>
                        <span
                          className="ff-point-item__dot"
                          style={{ background: BLUE }}
                        />
                        <span className="ff-point-item__label">{p.label}</span>
                        <span className="ff-point-item__coords">
                          {p.lat.toFixed(6)}, {p.lng.toFixed(6)}
                        </span>
                        <button
                          type="button"
                          className="ff-point-item__remove"
                          title={`Remove ${p.label}`}
                          onClick={() => removePoint(p.label)}
                        >
                          &times;
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {points.length > 0 && (
                <button
                  type="button"
                  className="ff-btn ff-btn--ghost ff-btn--sm"
                  style={{ marginTop: 10 }}
                  onClick={clearPoints}
                >
                  Clear all points
                </button>
              )}
            </div>
          )}

          {/* Advance */}
          <div className="ff-actions ff-actions--end">
            <button
              type="button"
              className="ff-btn ff-btn--primary"
              onClick={() => goStep(2)}
              disabled={points.length < 1}
            >
              Next: Enter data
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
