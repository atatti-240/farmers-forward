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

import Arrow from '../components/Arrow.jsx'
import { DEMO_CAPTION, demoField } from './demo.js'
import FarmFinder, { FarmFinderPins } from './FarmFinder.jsx'
import { autoGrid, gridCells, pointInPolygon } from './geo.js'
import GridLines from './GridLines.jsx'
import { ESRI_ATTR, ESRI_URL, FIELD_BLUE, START_CENTER, START_ZOOM } from './mapConfig.js'
import { InvalidateSizeOnVisible, TrackMapView } from './mapView.js'
import StepHead from './StepHead.jsx'
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

export default function Step1Field({ onFieldStarted }) {
  const {
    farmName, setFarmName, polygon, setPolygon, grid, setGrid, baselinePoints,
    setBaselinePoints, rows, setRows, cols, setCols, points, setGridPoints, addPoint,
    updatePoint, removePoint, clearPoints, tweakMode, setTweakMode, data, mapView,
    setMapView, goStep, loadDemoField,
  } = useTool()

  const [map, setMap] = useState(null)
  const [verts, setVerts] = useState([])
  const closed = polygon.length >= 3

  useEffect(() => {
    onFieldStarted?.(closed || verts.length > 0)
  }, [onFieldStarted, closed, verts.length])

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

  const hint = !closed
    ? verts.length === 0
      ? 'Click the map to place the first corner of your field'
      : verts.length < 3
        ? `Corner ${verts.length} placed · add at least ${3 - verts.length} more`
        : `${verts.length} corners · close the outline when you’re done`
    : points.length === 0
      ? 'Click inside the field to add a sample point'
      : `${points.length} sample point${points.length === 1 ? '' : 's'} · click inside the field to add more`

  return (
    <section className="step" aria-labelledby="step1-title">
      <StepHead id="step1-title" num={closed ? '02' : '01'} kicker={closed ? 'Field' : 'Find'}
        title={closed ? <>Lay out your <em>samples.</em></> : <>Find your field, then <em>trace it.</em></>}>
        {closed
          ? 'Each square of the grid gets one sample point. Change the grid, drag points to where you actually sampled, or click inside the field to add your own.'
          : 'Search an address or coordinates, then click each corner of one field on the satellite map. We’ll split it into a grid with a sample point in every square.'}
      </StepHead>

      {!closed && (
        <div className="demo">
          <div className="demo__text">
            <p className="kicker">No field handy?</p>
            <p className="demo__title">Use a real field near Needville, Fort Bend County.</p>
            <p className="demo__meta mono">{DEMO_CAPTION}</p>
          </div>
          <button type="button" className="btn btn--accent btn--lg demo__btn" onClick={useDemo}>
            Load the example field <Arrow />
          </button>
        </div>
      )}

      <div className="workspace">
        <div className="workspace__map">
          <div className="map map--tall" role="region" aria-label="Satellite map. Click to place field corners and sample points.">
            <MapContainer
              ref={setMap}
              center={mapView ? mapView.center : START_CENTER}
              zoom={mapView ? mapView.zoom : START_ZOOM}
              scrollWheelZoom={false}
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
          <div className="map-float map-float--top">
            <FarmFinder map={map} />
          </div>
          <p className="map-hint mono" aria-live="polite">
            <span className="map-hint__dot" aria-hidden="true" />{hint}
          </p>
        </div>

        <aside className="sheet" aria-label="Field setup">
          <div className={`sheet__sec${closed ? ' is-done' : ' is-current'}`}>
            <h2 className="sheet__head">
              <span className="sheet__num mono" aria-hidden="true">{closed ? '✓' : '01'}</span>
              Trace the outline
            </h2>
            {!closed ? (
              <>
                <p className="help">
                  {verts.length === 0
                    ? 'Click each corner of your field on the map, in order.'
                    : `${verts.length} corner${verts.length === 1 ? '' : 's'} placed. Add at least 3, then close the outline.`}
                </p>
                <div className="sheet__row">
                  <button type="button" className="btn btn--primary btn--sm" onClick={closeBoundary} disabled={verts.length < 3}>
                    Close outline
                  </button>
                  <button type="button" className="btn btn--quiet btn--sm" onClick={() => setVerts((prev) => prev.slice(0, -1))} disabled={verts.length === 0}>
                    Undo corner
                  </button>
                  <button type="button" className="btn btn--quiet btn--sm" onClick={() => setVerts([])} disabled={verts.length === 0}>
                    Clear
                  </button>
                </div>
              </>
            ) : (
              <div className="sheet__row sheet__row--between">
                <p className="help">Outline closed · <span className="mono">{polygon.length}</span> corners</p>
                <button type="button" className="btn btn--quiet btn--sm" onClick={resetBoundary}>
                  Redraw
                </button>
              </div>
            )}
          </div>

          <div className={`sheet__sec${!closed ? ' is-waiting' : points.length ? ' is-done' : ' is-current'}`}>
            <h2 className="sheet__head">
              <span className="sheet__num mono" aria-hidden="true">{closed && points.length ? '✓' : '02'}</span>
              Sampling grid
            </h2>
            {closed ? (
              <>
                <div className="grid-set">
                  <div className="field">
                    <label className="label" htmlFor="grid-rows">Rows</label>
                    <input id="grid-rows" className="input input--num mono" type="number" min="1" max="12" value={rows}
                      onChange={(e) => setRows(clampInt(e.target.value))} />
                  </div>
                  <span className="grid-set__x mono" aria-hidden="true">×</span>
                  <div className="field">
                    <label className="label" htmlFor="grid-cols">Columns</label>
                    <input id="grid-cols" className="input input--num mono" type="number" min="1" max="12" value={cols}
                      onChange={(e) => setCols(clampInt(e.target.value))} />
                  </div>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={applyGrid}>Apply</button>
                </div>
                <div className="sheet__checks">
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
                  Click inside the field to add a point; click a point to remove it.
                  {points.length >= MAX_POINTS && ` That’s the maximum of ${MAX_POINTS} points.`}
                </p>
              </>
            ) : (
              <p className="help">Close the outline first.</p>
            )}
          </div>

          <div className="sheet__sec">
            <div className="field">
              <label className="label" htmlFor="farm-name">Field name <span className="label__opt">(optional)</span></label>
              <input
                id="farm-name"
                className="input"
                type="text"
                maxLength={100}
                value={farmName}
                placeholder="North pasture"
                onChange={(e) => setFarmName(e.target.value)}
              />
            </div>
          </div>

          <div className="sheet__foot">
            <p className="sheet__count" aria-live="polite">
              <span className="sheet__big mono">{points.length}</span>
              <span className="help">
                {points.length > 0
                  ? `sample point${points.length === 1 ? '' : 's'} ready`
                  : closed ? 'Add at least one point' : 'Trace a field to continue'}
              </span>
            </p>
            <button type="button" className="btn btn--primary btn--lg" onClick={() => goStep(2)} disabled={points.length < 1}>
              Enter readings <Arrow />
            </button>
          </div>
        </aside>
      </div>
    </section>
  )
}
