/* ==========================================================================
   Farmers Forward (public site) - Step 1: Your field
   Adapted from the admin dashboard's field setup. Find the farm, click the
   satellite map to trace the field, close it, then lay a rows × columns grid
   with a sample point in every cell (or click to add points by hand).
   "Try an example field" loads a real Fort Bend County field in one click.
   ========================================================================== */

import { useEffect, useMemo, useRef, useState } from 'react'
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

import { demoField } from './demo.js'
import FarmFinder, { FarmFinderPins } from './FarmFinder.jsx'
import { autoGrid, gridCells, pointInPolygon } from './geo.js'
import GridLines from './GridLines.jsx'
import { ESRI_ATTR, ESRI_URL, FIELD_BLUE, START_CENTER, START_ZOOM } from './mapConfig.js'
import { InvalidateSizeOnVisible, TrackMapView } from './mapView.js'
import { MAX_POINTS, useTool } from './ToolProvider.jsx'

function dotIcon(label) {
  return L.divIcon({
    className: 'pt-divicon',
    html: `<span class="pt-dot" style="background:${FIELD_BLUE}"></span><span class="pt-label">${label}</span>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  })
}

/** Map clicks, minus the click that only dismissed a popup. */
function MapClicks({ onClick }) {
  const dismissing = useRef(false)
  useMapEvents({
    popupclose() {
      dismissing.current = true
      setTimeout(() => { dismissing.current = false }, 0)
    },
    click(e) {
      if (dismissing.current) return
      onClick(e.latlng.lat, e.latlng.lng)
    },
  })
  return null
}

function PointPopup({ point, onRemove }) {
  return (
    <Popup className="finder-popup">
      <div className="finder-popup__title">Point {point.label}</div>
      <p className="finder-popup__coords">{point.lat.toFixed(6)}, {point.lng.toFixed(6)}</p>
      <button
        type="button"
        className="btn btn--ghost btn--sm finder-popup__remove"
        onClick={(event) => { event.stopPropagation(); onRemove(point.label) }}
      >
        &times; Remove point
      </button>
    </Popup>
  )
}

/** Fit the view to the field whenever a new closed boundary appears. */
function FitPolygon({ polygon, hasSavedView }) {
  const map = useMap()
  const prevKey = useRef(hasSavedView ? JSON.stringify(polygon) : '')
  useEffect(() => {
    const key = JSON.stringify(polygon)
    if (polygon.length >= 3 && key !== prevKey.current) {
      try { map.fitBounds(polygon, { padding: [30, 30], maxZoom: 17 }) } catch { /* ignore */ }
    }
    prevKey.current = key
  }, [map, polygon])
  return null
}

function clampInt(value) {
  const v = parseInt(value, 10)
  if (Number.isNaN(v)) return ''
  return Math.min(12, Math.max(1, v))
}

export default function Step1Field() {
  const {
    farmName, setFarmName, polygon, setPolygon, grid, setGrid, baselinePoints,
    setBaselinePoints, rows, setRows, cols, setCols, points, setGridPoints, addPoint,
    updatePoint, removePoint, clearPoints, tweakMode, setTweakMode, data, mapView,
    setMapView, goStep, loadDemoField,
  } = useTool()

  const [map, setMap] = useState(null)
  const [verts, setVerts] = useState([])
  const closed = polygon.length >= 3

  const cells = useMemo(
    () => (closed && grid ? gridCells(polygon, grid.rows, grid.cols) : []),
    [closed, polygon, grid])

  function handleMapClick(lat, lng) {
    if (!closed) setVerts((prev) => [...prev, [lat, lng]])
    else if (pointInPolygon([lat, lng], polygon)) addPoint(lat, lng)
  }

  function closeBoundary() {
    if (verts.length < 3) return
    setPolygon(verts)
    setVerts([])
    // A sensible starting grid straight away; Apply changes it.
    const next = { rows: rows || 3, cols: cols || 3 }
    setGrid(next)
    setGridPoints(baselinePoints ? autoGrid(verts, next.rows, next.cols) : [])
  }

  function resetBoundary() {
    setPolygon([])
    setGrid(null)
    clearPoints()
    setVerts([])
  }

  function applyGrid() {
    const next = { rows: rows || 3, cols: cols || 3 }
    setGrid(next)
    setGridPoints(baselinePoints ? autoGrid(polygon, next.rows, next.cols) : [])
  }

  function toggleBaselinePoints(on) {
    if (!grid) { setBaselinePoints(on); return }
    if (on) {
      setBaselinePoints(true)
      setGridPoints(autoGrid(polygon, grid.rows, grid.cols))
      return
    }
    const hasData = points.some((p) => p.cell &&
      Object.values(data[p.label] || {}).some((v) => String(v ?? '').trim() !== ''))
    if (hasData && !window.confirm(
      'Turning off grid points also clears the soil readings entered for them. Continue?')) return
    setBaselinePoints(false)
    setGridPoints([])
  }

  function useDemo() {
    setVerts([])
    loadDemoField(demoField())
  }

  return (
    <section aria-labelledby="step1-title">
      <div className="step-head">
        <p className="eyebrow">Step 1 of 4</p>
        <h1 id="step1-title" className="step-head__title">Map your field</h1>
        <p className="step-head__lead">
          Find your farm, then click the map to trace the edge of one field.
          We’ll split it into a grid and put a sample point in each square.
        </p>
        <div className="demo-callout">
          <p>
            <strong>No field handy?</strong> Load a real field in Fort Bend
            County, Texas and fill it with example readings in the next step.
          </p>
          <button type="button" className="btn btn--glow" onClick={useDemo}>
            Try an example field
          </button>
        </div>
      </div>

      <div className="split">
        <div className="map-col">
          <FarmFinder map={map} />
          <div className="map" role="region" aria-label="Satellite map. Click to place field corners and sample points.">
            <MapContainer
              ref={setMap}
              center={mapView ? mapView.center : START_CENTER}
              zoom={mapView ? mapView.zoom : START_ZOOM}
              scrollWheelZoom
            >
              <TileLayer url={ESRI_URL} attribution={ESRI_ATTR} maxZoom={19} />
              <TrackMapView onChange={setMapView} />
              <InvalidateSizeOnVisible />
              <MapClicks onClick={handleMapClick} />
              <FitPolygon polygon={polygon} hasSavedView={!!mapView} />
              <FarmFinderPins />

              {!closed && verts.length > 0 && (
                <>
                  <Polyline positions={verts} pathOptions={{ color: FIELD_BLUE, weight: 2, dashArray: '5,6' }} />
                  {verts.map((v, i) => (
                    <CircleMarker
                      key={`v${i}`}
                      center={v}
                      radius={5}
                      pathOptions={{ color: '#fff', weight: 2, fillColor: FIELD_BLUE, fillOpacity: 1 }}
                    />
                  ))}
                </>
              )}

              {cells.length > 0 && <GridLines cells={cells} />}

              {closed && (
                <Polygon
                  positions={polygon}
                  pathOptions={{ color: FIELD_BLUE, weight: 2, fillColor: FIELD_BLUE, fillOpacity: 0.12 }}
                />
              )}

              {closed && points.map((p) => (tweakMode ? (
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
              ) : (
                <CircleMarker
                  key={p.label}
                  center={[p.lat, p.lng]}
                  radius={7}
                  bubblingMouseEvents={false}
                  pathOptions={{ color: '#fff', weight: 2, fillColor: FIELD_BLUE, fillOpacity: 1 }}
                >
                  <Tooltip permanent direction="top" className="label-tip">{p.label}</Tooltip>
                  <PointPopup point={p} onRemove={removePoint} />
                </CircleMarker>
              )))}
            </MapContainer>
          </div>
        </div>

        <div className="side">
          <div className="panel">
            <div className="field">
              <label className="label" htmlFor="farm-name">Field name <span className="help">(optional)</span></label>
              <input
                id="farm-name"
                className="input"
                type="text"
                maxLength={100}
                value={farmName}
                placeholder="e.g. North pasture"
                onChange={(e) => setFarmName(e.target.value)}
              />
            </div>
          </div>

          <div className="panel">
            <h2 className="panel__title">
              <span className={`panel__num${closed ? ' panel__num--done' : ''}`} aria-hidden="true">{closed ? '✓' : '1'}</span>
              Trace the field
            </h2>
            {!closed ? (
              <>
                <p className="help">
                  {verts.length === 0
                    ? 'Click each corner of your field on the map.'
                    : `${verts.length} corner${verts.length === 1 ? '' : 's'} placed. Add at least 3, then close the outline.`}
                </p>
                <div className="inline">
                  <button type="button" className="btn btn--primary btn--sm" onClick={closeBoundary} disabled={verts.length < 3}>
                    Close outline
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setVerts((prev) => prev.slice(0, -1))} disabled={verts.length === 0}>
                    Undo corner
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setVerts([])} disabled={verts.length === 0}>
                    Clear
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="help">Outline closed ({polygon.length} corners).</p>
                <button type="button" className="btn btn--secondary btn--sm" onClick={resetBoundary}>
                  Redraw outline
                </button>
              </>
            )}
          </div>

          {closed && (
            <div className="panel">
              <h2 className="panel__title">
                <span className={`panel__num${points.length ? ' panel__num--done' : ''}`} aria-hidden="true">{points.length ? '✓' : '2'}</span>
                Sampling grid
              </h2>
              <div className="num-group">
                <div className="field">
                  <label className="label" htmlFor="grid-rows">Rows</label>
                  <input id="grid-rows" className="input input--num" type="number" min="1" max="12" value={rows}
                    onChange={(e) => setRows(clampInt(e.target.value))} />
                </div>
                <span className="num-group__x" aria-hidden="true">×</span>
                <div className="field">
                  <label className="label" htmlFor="grid-cols">Columns</label>
                  <input id="grid-cols" className="input input--num" type="number" min="1" max="12" value={cols}
                    onChange={(e) => setCols(clampInt(e.target.value))} />
                </div>
                <button type="button" className="btn btn--secondary btn--sm" onClick={applyGrid}>Apply grid</button>
              </div>
              <div className="inline inline--checks">
                <label className="check">
                  <input type="checkbox" checked={baselinePoints} onChange={(e) => toggleBaselinePoints(e.target.checked)} />
                  A point in every square
                </label>
                <label className="check">
                  <input type="checkbox" checked={tweakMode} disabled={points.length === 0} onChange={(e) => setTweakMode(e.target.checked)} />
                  Drag points to adjust
                </label>
              </div>
              <p className="help">
                Click inside the field to add your own point. Click a point to remove it.
                {points.length >= MAX_POINTS && ` That’s the maximum of ${MAX_POINTS} points.`}
              </p>
            </div>
          )}

          <div className="side__foot">
            <p className="help" aria-live="polite">
              {points.length > 0
                ? `${points.length} sample point${points.length === 1 ? '' : 's'} ready.`
                : closed ? 'Add at least one sample point to continue.' : 'Trace a field to continue.'}
            </p>
            <button type="button" className="btn btn--primary" onClick={() => goStep(2)} disabled={points.length < 1}>
              Next: soil readings →
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}
