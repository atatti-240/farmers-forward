/* ==========================================================================
   Farmers Forward (public site) - Step 2: Soil readings
   Adapted from the admin data-entry step. Pick a point, choose its crop and
   type the five sensor readings; each point (and its grid square) takes its
   soil health colour as soon as its readings are complete. Out-of-range
   values are flagged inline with the range the server accepts.
   ========================================================================== */

import { useEffect, useRef, useState } from 'react'
import { MapContainer, Pane, TileLayer, Polygon, CircleMarker, Tooltip, useMap } from 'react-leaflet'

import Arrow from '../components/Arrow.jsx'
import CropPicker from './CropPicker.jsx'
import { exampleReadings } from './demo.js'
import { gridCellRing } from './geo.js'
import { ESRI_ATTR, ESRI_URL, FIELD_BLUE, START_CENTER, START_ZOOM } from './mapConfig.js'
import { InvalidateSizeOnVisible, TrackMapView } from './mapView.js'
import ScoreLegend from './ScoreLegend.jsx'
import { scoreColor } from './soilColor.js'
import StepHead from './StepHead.jsx'
import { readingError, useTool } from './ToolProvider.jsx'

export const FIELDS = [
  { key: 'ph', label: 'pH', unit: '', hint: 'Acidity, 0–14', placeholder: '6.5' },
  { key: 'ec', label: 'EC', unit: 'dS/m', hint: 'Salinity, 1:2 soil:water', placeholder: '0.8' },
  { key: 'nitrogen', label: 'Nitrogen', unit: 'ppm', hint: 'Nitrate-N', placeholder: '20' },
  { key: 'phosphorus', label: 'Phosphorus', unit: 'ppm', hint: 'Available P', placeholder: '25' },
  { key: 'potassium', label: 'Potassium', unit: 'ppm', hint: 'Available K', placeholder: '180' },
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
    <section className="step" aria-labelledby="step2-title">
      <StepHead id="step2-title" num="03" kicker="Readings" title={<>Enter the <em>readings.</em></>}
        aside={(
          <div className="meter" aria-label={`${completeCount} of ${total} points complete`}>
            <p className="meter__text">
              <span className="meter__big mono">{completeCount}</span>
              <span className="mono meter__of">/ {total} points complete</span>
            </p>
            <div className="meter__track" aria-hidden="true">
              <span className="meter__fill" style={{ transform: `scaleX(${pct / 100})` }} />
            </div>
          </div>
        )}>
        Pick a point, choose its crop and type the five sensor readings. Each
        point takes its soil health colour the moment it’s complete.
      </StepHead>

      <div className="workspace">
        <div className="workspace__map">
          <div className="map map--tall" role="region" aria-label="Map of sample points coloured by soil health">
            <MapContainer
              center={mapView ? mapView.center : START_CENTER}
              zoom={mapView ? mapView.zoom : START_ZOOM}
              scrollWheelZoom={false}
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
        </div>

        <aside className="sheet" aria-label="Soil readings">
          <div className="sheet__sec">
            <div className="sheet__row sheet__row--between">
              <h2 className="sheet__head sheet__head--plain">Sample points</h2>
              <button type="button" className="btn btn--accent btn--sm" onClick={fillExamples}>
                Fill example readings
              </button>
            </div>
            {filledNote && <p className="note" role="status">{filledNote}</p>}
            <ul className="chips" aria-label="Sample points">
              {points.map((p) => {
                const complete = sectionComplete(p.label)
                const isActive = p.label === selected
                return (
                  <li key={p.label}>
                    <button
                      type="button"
                      className={`chip${isActive ? ' chip--active' : ''}${complete ? ' chip--done' : ''}`}
                      aria-pressed={isActive}
                      aria-label={`Point ${p.label}, ${complete ? 'complete' : 'needs readings'}`}
                      onClick={() => setSelected(p.label)}
                    >
                      <span className="chip__dot" style={{ background: scoreColor(scores[p.label]) }} aria-hidden="true" />
                      <span className="mono">{p.label}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>

          {active && (
            <div className="sheet__sec entry">
              <div className="entry__head">
                <h3 className="h3 entry__title">Point <span className="mono">{active.label}</span></h3>
                <span className="entry__coords mono">{active.lat.toFixed(5)}, {active.lng.toFixed(5)}</span>
              </div>

              <CropPicker
                key={active.label}
                value={activeData.crop_type ?? ''}
                onChange={(crop) => setSectionData(active.label, 'crop_type', crop)}
              />
              {points.length > 1 && (activeData.crop_type || '').trim() && (
                <button type="button" className="btn btn--link entry__all" onClick={cropForAll}>
                  Use {activeData.crop_type.trim()} for every point
                </button>
              )}

              <div className="readings-form">
                {FIELDS.map((f) => {
                  const value = activeData[f.key] ?? ''
                  const err = readingError(f.key, value)
                  const id = `in-${f.key}`
                  return (
                    <div key={f.key} className={`reading${err ? ' reading--bad' : ''}`}>
                      <label className="reading__label" htmlFor={id}>
                        <span className="reading__name">{f.label}</span>
                        <span className="reading__hint" id={`${id}-hint`}>{f.hint}</span>
                      </label>
                      <div className="reading__box">
                        <input
                          id={id}
                          className={`input reading__input mono${err ? ' input--bad' : ''}`}
                          type="number"
                          step="any"
                          min="0"
                          inputMode="decimal"
                          placeholder={f.placeholder}
                          value={value}
                          aria-invalid={err ? true : undefined}
                          aria-describedby={err ? `${id}-err` : `${id}-hint`}
                          onChange={(e) => setSectionData(active.label, f.key, e.target.value)}
                        />
                        <span className="reading__unit mono" aria-hidden="true">{f.unit}</span>
                      </div>
                      {err && <span id={`${id}-err`} className="error-text reading__err">{err}</span>}
                    </div>
                  )
                })}
              </div>

              <div className="sheet__row sheet__row--between">
                <p className="help">
                  {sectionComplete(active.label)
                    ? <><span className="ok-dot" aria-hidden="true" /> Point {active.label} is complete.</>
                    : 'Choose a crop and enter all five readings.'}
                </p>
                {completeCount < total && (
                  <button type="button" className="btn btn--ghost btn--sm" onClick={nextIncomplete}>
                    Next empty point <Arrow />
                  </button>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>

      <div className="actions">
        <button type="button" className="btn btn--quiet btn--back" onClick={() => goStep(1)}><Arrow back /> Field</button>
        <span className="actions__spacer" />
        <button type="button" className="btn btn--primary btn--lg" onClick={() => goStep(3)} disabled={points.length < 1}>
          Check and run <Arrow />
        </button>
      </div>
    </section>
  )
}
