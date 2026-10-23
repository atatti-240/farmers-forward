/* ==========================================================================
   Farmers Forward (public site) - Step 2: Soil readings
   Adapted from the admin data-entry step. Pick a point, choose its crop and
   type the five sensor readings; each point (and its grid square) takes its
   soil health colour as soon as its readings are complete. Out-of-range
   values are flagged inline with the range the server accepts.
   ========================================================================== */

import { useEffect, useRef, useState } from 'react'
import { MapContainer, Pane, TileLayer, Polygon, CircleMarker, Tooltip, useMap } from 'react-leaflet'

import CropPicker from './CropPicker.jsx'
import { exampleReadings } from './demo.js'
import { gridCellRing } from './geo.js'
import { ESRI_ATTR, ESRI_URL, FIELD_BLUE, START_CENTER, START_ZOOM } from './mapConfig.js'
import { InvalidateSizeOnVisible, TrackMapView } from './mapView.js'
import ScoreLegend from './ScoreLegend.jsx'
import { scoreColor } from './soilColor.js'
import { readingError, useTool } from './ToolProvider.jsx'

export const FIELDS = [
  { key: 'ph', label: 'pH', unit: '', hint: 'Acidity, 0–14', placeholder: '6.5' },
  { key: 'ec', label: 'EC', unit: 'dS/m', hint: 'Salinity, 1:2 soil:water', placeholder: '0.8' },
  { key: 'nitrogen', label: 'Nitrogen (N)', unit: 'ppm', hint: 'Nitrate-N', placeholder: '20' },
  { key: 'phosphorus', label: 'Phosphorus (P)', unit: 'ppm', hint: '', placeholder: '25' },
  { key: 'potassium', label: 'Potassium (K)', unit: 'ppm', hint: '', placeholder: '180' },
]

function FitView({ polygon, points }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    if (done.current) return
    const positions = polygon.length >= 2 ? polygon : points.map((p) => [p.lat, p.lng])
    if (positions.length >= 2) {
      try {
        map.fitBounds(positions, { padding: [30, 30], maxZoom: 17 })
        done.current = true
      } catch { /* ignore */ }
    }
  }, [map, polygon, points])
  return null
}

export default function Step2Readings() {
  const {
    polygon, points, data, setSectionData, sectionComplete, scores, completeCount,
    total, mapView, setMapView, goStep, fillData,
  } = useTool()
  const [selected, setSelected] = useState(null)
  const [filledNote, setFilledNote] = useState('')

  useEffect(() => {
    if (points.length === 0) return
    if (!selected || !points.some((p) => p.label === selected)) setSelected(points[0].label)
  }, [points, selected])

  const active = points.find((p) => p.label === selected) || null
  const activeData = (active && data[active.label]) || {}
  const pct = total > 0 ? Math.round((completeCount / total) * 100) : 0

  function cropForAll() {
    const crop = (activeData.crop_type || '').trim()
    if (!crop) return
    const patch = {}
    for (const p of points) patch[p.label] = { crop_type: crop }
    fillData(patch)
    setFilledNote(`${crop} set for all ${points.length} points.`)
  }

  function fillExamples() {
    const crop = (activeData.crop_type || '').trim() || 'Corn'
    const empty = points.filter((p) => !sectionComplete(p.label))
    const target = empty.length > 0 ? empty : points
    fillData(exampleReadings(target, crop))
    setFilledNote(`Example readings added to ${target.length} point${target.length === 1 ? '' : 's'}. They’re made up for this demo, so swap in your own when you have them.`)
  }

  function nextIncomplete() {
    const i = points.findIndex((p) => p.label === selected)
    const order = [...points.slice(i + 1), ...points.slice(0, i + 1)]
    const next = order.find((p) => !sectionComplete(p.label))
    if (next) setSelected(next.label)
  }

  return (
    <section aria-labelledby="step2-title">
      <div className="step-head">
        <p className="eyebrow">Step 2 of 4</p>
        <h1 id="step2-title" className="step-head__title">Enter soil readings</h1>
        <p className="step-head__lead">
          Pick a point on the map or in the list, choose the crop, and type
          its five sensor readings. Each point lights up with its soil health
          as soon as it’s complete.
        </p>
      </div>

      <div className="progress" aria-label={`${completeCount} of ${total} points complete`}>
        <div className="progress__meta">
          <span>Points complete</span>
          <span className="mono">{completeCount} / {total}</span>
        </div>
        <div className="progress__track" aria-hidden="true">
          <div className="progress__bar" style={{ width: `${pct}%` }} />
        </div>
      </div>

      <div className="split">
        <div className="map" role="region" aria-label="Map of sample points coloured by soil health">
          <MapContainer
            center={mapView ? mapView.center : START_CENTER}
            zoom={mapView ? mapView.zoom : START_ZOOM}
            scrollWheelZoom
          >
            <TileLayer url={ESRI_URL} attribution={ESRI_ATTR} maxZoom={19} />
            <TrackMapView onChange={setMapView} />
            <InvalidateSizeOnVisible />
            <ScoreLegend showNoData />
            {!mapView && <FitView polygon={polygon} points={points} />}
            {polygon.length >= 3 && (
              <Polygon positions={polygon} pathOptions={{
                color: FIELD_BLUE, weight: 2, fillColor: FIELD_BLUE, fillOpacity: 0.08, interactive: false,
              }} />
            )}
            <Pane name="score-cells" style={{ zIndex: 390 }}>
              {points.map((p) => {
                if (scores[p.label] === undefined) return null
                const ring = gridCellRing(polygon, p.cell)
                if (!ring) return null
                const color = scoreColor(scores[p.label])
                return (
                  <Polygon key={`cell-${p.label}`} positions={ring}
                    pathOptions={{ color, weight: 1, opacity: 0.6, fillColor: color, fillOpacity: 0.4 }}
                    eventHandlers={{ click: () => setSelected(p.label) }} />
                )
              })}
            </Pane>
            {points.map((p) => {
              const isActive = p.label === selected
              return (
                <CircleMarker key={p.label} center={[p.lat, p.lng]} radius={isActive ? 10 : 7}
                  pathOptions={{
                    color: isActive ? '#ffffff' : 'rgba(255,255,255,0.85)',
                    weight: isActive ? 4 : 2,
                    fillColor: scoreColor(scores[p.label]),
                    fillOpacity: 1,
                  }}
                  eventHandlers={{ click: () => setSelected(p.label) }}>
                  <Tooltip permanent direction="top" className="label-tip">{p.label}</Tooltip>
                </CircleMarker>
              )
            })}
          </MapContainer>
        </div>

        <div className="side">
          <div className="panel">
            <div className="inline inline--between">
              <h2 className="panel__title panel__title--plain">Sample points</h2>
              <button type="button" className="btn btn--ghost btn--sm" onClick={fillExamples}>
                Fill example readings
              </button>
            </div>
            {filledNote && <p className="note" role="status">{filledNote}</p>}
            <ul className="chips-list" aria-label="Sample points">
              {points.map((p) => {
                const complete = sectionComplete(p.label)
                const isActive = p.label === selected
                return (
                  <li key={p.label}>
                    <button
                      type="button"
                      className={`pt-chip${isActive ? ' pt-chip--active' : ''}`}
                      aria-pressed={isActive}
                      aria-label={`Point ${p.label}, ${complete ? 'complete' : 'needs readings'}`}
                      onClick={() => setSelected(p.label)}
                    >
                      <span className="pt-chip__dot" style={{ background: scoreColor(scores[p.label]) }} aria-hidden="true" />
                      {p.label}
                      {complete && <span className="pt-chip__tick" aria-hidden="true">✓</span>}
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>

          {active && (
            <div className="panel panel--entry">
              <div className="entry-head">
                <span className="entry-head__label">Point {active.label}</span>
                <span className="entry-head__coords mono">{active.lat.toFixed(5)}, {active.lng.toFixed(5)}</span>
              </div>

              <CropPicker
                key={active.label}
                value={activeData.crop_type ?? ''}
                onChange={(crop) => setSectionData(active.label, 'crop_type', crop)}
              />
              {points.length > 1 && (activeData.crop_type || '').trim() && (
                <button type="button" className="btn btn--link" onClick={cropForAll}>
                  Use {activeData.crop_type.trim()} for every point
                </button>
              )}

              <div className="entry-grid">
                {FIELDS.map((f) => {
                  const value = activeData[f.key] ?? ''
                  const err = readingError(f.key, value)
                  const id = `in-${f.key}`
                  return (
                    <div key={f.key} className={`field${f.key === 'potassium' ? ' field--full' : ''}`}>
                      <label className="label" htmlFor={id}>
                        {f.label} {f.unit && <span className="label__unit">{f.unit}</span>}
                      </label>
                      <input
                        id={id}
                        className={`input mono${err ? ' input--bad' : ''}`}
                        type="number"
                        step="any"
                        min="0"
                        inputMode="decimal"
                        placeholder={f.placeholder}
                        value={value}
                        aria-invalid={err ? true : undefined}
                        aria-describedby={err ? `${id}-err` : f.hint ? `${id}-hint` : undefined}
                        onChange={(e) => setSectionData(active.label, f.key, e.target.value)}
                      />
                      {err
                        ? <span id={`${id}-err`} className="error-text">{err}</span>
                        : f.hint && <span id={`${id}-hint`} className="help">{f.hint}</span>}
                    </div>
                  )
                })}
              </div>

              <div className="inline inline--between">
                <p className="help">
                  {sectionComplete(active.label)
                    ? 'This point is complete.'
                    : 'Choose a crop and enter all five readings.'}
                </p>
                {completeCount < total && (
                  <button type="button" className="btn btn--ghost btn--sm" onClick={nextIncomplete}>
                    Next empty point →
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="actions">
        <button type="button" className="btn btn--secondary" onClick={() => goStep(1)}>← Back</button>
        <span className="actions__spacer" />
        <button type="button" className="btn btn--primary" onClick={() => goStep(3)} disabled={points.length < 1}>
          Review →
        </button>
      </div>
    </section>
  )
}
