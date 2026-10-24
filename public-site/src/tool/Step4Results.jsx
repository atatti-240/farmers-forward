/* ==========================================================================
   Farmers Forward (public site) - Step 4: Results
   Renders the POST /assessments response (kept in memory only): the field's
   overall score, the satellite map with every grid square coloured by soil
   health, whole-field panels, and one card per region with headed sections
   (what's wrong, what to do, fertilizer, crops, soil, biology), plus the PDF
   download. The PDF is built server-side from the stored submission using
   the 1-hour token from that same response; the map is rasterised here and
   sent along. Green/amber/red appear ONLY on data (map squares, dots, scores).
   ========================================================================== */

import { useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, Pane, TileLayer, Polygon, CircleMarker, Tooltip, useMap } from 'react-leaflet'

import { downloadReport } from '../api/client.js'
import Arrow from '../components/Arrow.jsx'
import { gridCellRing, polygonCentroid } from './geo.js'
import { captureMapImage } from './mapCapture.js'
import { ESRI_ATTR, ESRI_URL, FIELD_BLUE, ROTATE_OPTIONS, START_CENTER } from './mapConfig.js'
import { InvalidateSizeOnVisible, MapBearing, fitTurned } from './mapView.js'
import {
  FertilizerPlan, FieldFertilizerTotal, SoilBiology, SoilProfile, hasFertilizerPlan,
  microbialLine, usableProfile,
} from './OptionalBlocks.jsx'
import ScoreLegend from './ScoreLegend.jsx'
import { scoreColor } from './soilColor.js'
import { useTool } from './ToolProvider.jsx'

const fmt = (v) => (Number.isFinite(Number(v)) ? Math.round(Number(v)) : '—')

const NUTRIENT_NAMES = { N: 'Nitrogen (N)', P: 'Phosphate (P₂O₅)', K: 'Potash (K₂O)' }

const READINGS = [
  ['ph', 'pH', ''],
  ['ec', 'EC', 'dS/m'],
  ['nitrogen', 'N', 'ppm'],
  ['phosphorus', 'P', 'ppm'],
  ['potassium', 'K', 'ppm'],
]

/** One sentence a farmer can read in two seconds. */
function headline(fs, results) {
  const n = results.length
  const good = results.filter((r) => Number(r.health_score) >= 70).length
  const poor = results.filter((r) => Number(r.health_score) < 40).length
  const parts = []
  if (good === n) parts.push(`All ${n} region${n === 1 ? ' is' : 's are'} in good shape.`)
  else if (poor === n) parts.push(`All ${n} region${n === 1 ? ' needs' : 's need'} attention.`)
  else parts.push(`${good} of ${n} region${n === 1 ? ' is' : 's are'} in good shape${poor ? `, ${poor} need${poor === 1 ? 's' : ''} attention` : ''}.`)
  if (fs.dominant_problem_code && fs.dominant_problem_code !== 'none') {
    parts.push(`The most common issue is ${String(fs.dominant_problem_label || '').toLowerCase()}.`)
  }
  return parts.join(' ')
}

/** Fit the map to the field once its container has a real size (it can be
 *  0×0 for a frame or two while the results page lays out). */
function FitView({ polygon, positions }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    const pts = polygon.length >= 3 ? polygon : positions
    if (done.current || pts.length < 2) return undefined
    let frame = 0
    let tries = 0
    const attempt = () => {
      const size = map.getSize()
      if ((size.x === 0 || size.y === 0) && tries++ < 30) {
        frame = requestAnimationFrame(attempt)
        return
      }
      map.invalidateSize()
      try {
        fitTurned(map, pts, { padding: 36, maxZoom: 18 })
        done.current = true
      } catch { /* ignore */ }
    }
    attempt()
    return () => cancelAnimationFrame(frame)
  }, [map, polygon, positions])
  return null
}

/** The field's average score as a large numeral inside a ring. */
function ScoreDial({ score, category }) {
  const [drawn, setDrawn] = useState(false)
  useEffect(() => {
    const id = requestAnimationFrame(() => setDrawn(true))
    return () => cancelAnimationFrame(id)
  }, [])
  const value = Number.isFinite(Number(score)) ? Math.max(0, Math.min(100, Number(score))) : 0
  const r = 96
  const c = 2 * Math.PI * r
  return (
    <figure className="dial" style={{ '--score': scoreColor(score) }}>
      <svg className="dial__ring" viewBox="0 0 220 220" aria-hidden="true">
        <circle className="dial__track" cx="110" cy="110" r={r} />
        <circle
          className="dial__arc"
          cx="110"
          cy="110"
          r={r}
          strokeDasharray={c}
          strokeDashoffset={drawn ? c * (1 - value / 100) : c}
        />
      </svg>
      <figcaption className="dial__body">
        <span className="dial__num">{fmt(score)}</span>
        <span className="dial__cat">{category || '—'}</span>
        <span className="dial__of mono">Average soil health · 0–100</span>
      </figcaption>
    </figure>
  )
}

function NutrientNeed({ assessment }) {
  if (!assessment || typeof assessment !== 'object') return null
  const rows = ['N', 'P', 'K']
    .map((k) => [k, assessment[k], assessment.units && assessment.units[k]])
    .filter(([, v]) => v && Number.isFinite(Number(v.add)))
  if (rows.length === 0) return null
  return (
    <>
      <dl className="need">
        {rows.map(([k, v, unit]) => (
          <div key={k} className="need__row">
            <dt>{NUTRIENT_NAMES[k]}</dt>
            <dd>
              {Number(v.add) > 0
                ? <><b className="mono">{Math.round(Number(v.add))}</b> <span className="need__unit">{unit || 'lbs/acre'}</span></>
                : <span className="need__none">None needed</span>}
            </dd>
          </div>
        ))}
      </dl>
      {assessment.crop && <p className="source">Texas A&amp;M soil-test table for {String(assessment.crop).toLowerCase()}.</p>}
    </>
  )
}

const TAGS_SHOWN = 8

/** Crop tags, the first few shown and the rest behind "+ N more". */
function Tags({ crops, strong = false }) {
  const [all, setAll] = useState(false)
  const shown = all ? crops : crops.slice(0, TAGS_SHOWN)
  return (
    <span className="tags">
      {shown.map((c) => <span key={c} className={`tag${strong ? ' tag--strong' : ''}`}>{c}</span>)}
      {crops.length > TAGS_SHOWN && (
        <button type="button" className="tag tag--more" onClick={() => setAll((v) => !v)} aria-expanded={all}>
          {all ? 'Show fewer' : `+ ${crops.length - TAGS_SHOWN} more`}
        </button>
      )}
    </span>
  )
}

function hasNutrientNeed(a) {
  return !!a && typeof a === 'object' &&
    ['N', 'P', 'K'].some((k) => a[k] && Number.isFinite(Number(a[k].add)))
}

function RegionCard({ r, highlighted, profile, microbial }) {
  const inputs = r.inputs || {}
  const recs = Array.isArray(r.recommendations) ? r.recommendations : []
  const suit = r.crop_suitability || {}
  const ideal = suit.ideal || []
  const adjust = suit.viable_with_adjustments || []
  const problem = r.primary_problem || {}
  const sc = r.selected_crop || {}
  const noProblem = !problem.code || problem.code === 'none'
  const bio = microbialLine(microbial)

  const sections = []
  sections.push(['What’s wrong', (
    <>
      {noProblem
        ? <p>No major problems for this crop. Keep doing what you’re doing.</p>
        : <p><b>{problem.label}.</b> {problem.message}</p>}
      {sc.note && <p className="help">{sc.note}</p>}
    </>
  )])
  if (recs.length > 0) {
    sections.push(['What to do', (
      <ol className="recs">
        {recs.map((rec, i) => (
          <li key={rec.fix_code || i} className="recs__item">
            <p className="recs__title">{rec.title || 'Action'}</p>
            {rec.action && <p className="recs__action">{rec.action}</p>}
            {(rec.rate || rec.timing || rec.cost_estimate) && (
              <dl className="recs__meta">
                {rec.rate && <div><dt>How much</dt><dd>{rec.rate}</dd></div>}
                {rec.timing && <div><dt>When</dt><dd>{rec.timing}</dd></div>}
                {rec.cost_estimate && <div><dt>Rough cost</dt><dd>{rec.cost_estimate}</dd></div>}
              </dl>
            )}
          </li>
        ))}
      </ol>
    )])
  }
  if (hasFertilizerPlan(r.fertilizer_plan) || hasNutrientNeed(r.crop_assessment)) {
    sections.push(['Fertilizer plan', (
      <>
        <NutrientNeed assessment={r.crop_assessment} />
        <FertilizerPlan plan={r.fertilizer_plan} />
      </>
    )])
  }
  sections.push(['Crops that fit now', ideal.length + adjust.length === 0 ? (
    <p className="help">None of the crops we check fit these readings yet. Start with the fixes above.</p>
  ) : (
    <>
      {ideal.length > 0 && (
        <div className="crop-row">
          <span className="label">Best fit</span>
          <Tags crops={ideal} strong />
        </div>
      )}
      {adjust.length > 0 && (
        <div className="crop-row">
          <span className="label">With some fixes</span>
          <Tags crops={adjust} />
        </div>
      )}
    </>
  )])
  if (profile) {
    const bits = [profile.series, profile.texture, profile.drainage].filter((v) => typeof v === 'string' && v.trim())
    if (bits.length) {
      sections.push(['Soil profile', (
        <p>{bits.join(' · ')} <span className="help">(mapped for the whole field)</span></p>
      )])
    }
  }
  if (bio) sections.push(['Microbial activity', <p>{bio} <span className="help">(whole field)</span></p>])

  return (
    <article
      id={`region-${r.section_label}`}
      className={`region${highlighted ? ' region--flash' : ''}`}
      style={{ '--score': scoreColor(r.health_score) }}
      aria-labelledby={`region-${r.section_label}-title`}
      tabIndex={-1}
    >
      <div className="region__side">
        <div className="region__sticky">
          <p className="kicker">Region</p>
          <h3 id={`region-${r.section_label}-title`} className="region__label">
            <span className="visually-hidden">Region </span>{r.section_label}
          </h3>
          <p className="region__score">
            <span className="region__dot" aria-hidden="true" />
            <span className="region__num mono">{fmt(r.health_score)}</span>
            <span className="region__cat">{r.health_category}</span>
          </p>
          <p className="region__crop">Growing <b>{r.crop_type || 'an unrecorded crop'}</b></p>
          <dl className="region__readings" aria-label="Readings entered">
            {READINGS.map(([key, label, unit]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd className="mono">{inputs[key] ?? '—'}{unit && <span className="region__unit"> {unit}</span>}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      <div className="region__body">
        {sections.map(([title, body], i) => (
          <section key={title} className="rblock">
            <h4 className="rblock__head">
              <span className="rblock__num mono" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
              {title}
            </h4>
            <div className="rblock__body">{body}</div>
          </section>
        ))}

        <details className="more">
          <summary>More detail</summary>
          <div className="more__body">
            {r.score_basis === 'crop' && (
              <p className="help">
                This score is specific to {String(sc.profile_name || r.crop_type).toLowerCase()}. General
                soil health (any crop): <b className="mono">{fmt(r.general_health_score)}</b> ({r.general_health_category}).
              </p>
            )}
            {sc.basis && <p className="help">{sc.basis}</p>}
            {Array.isArray(sc.limiting_factors) && sc.limiting_factors.length > 0 && (
              <ul className="bullets">{sc.limiting_factors.map((f) => <li key={f}>{f}</li>)}</ul>
            )}
            {recs.filter((rec) => rec.notes).map((rec, i) => (
              <p key={i} className="help"><b>{rec.title}:</b> {rec.notes}</p>
            ))}
            {inputs.ece != null && (
              <p className="help">EC entered as a 1:2 soil:water reading ({inputs.ec} dS/m), analyzed as ECe ≈ {inputs.ece} dS/m.</p>
            )}
          </div>
        </details>
      </div>
    </article>
  )
}

export default function Step4Results() {
  const { result, resetAll, editAgain } = useTool()
  const mapRef = useRef(null)
  const [leafletMap, setLeafletMap] = useState(null)
  const [download, setDownload] = useState({ state: 'idle', message: '' })
  const [flash, setFlash] = useState(null)

  const analysis = result && result.analysis
  const polygon = (result && result.polygon) || []
  const points = useMemo(() => (result && result.points) || [], [result])
  const results = useMemo(() => (analysis && Array.isArray(analysis.results) ? analysis.results : []), [analysis])

  const positions = useMemo(() => results
    .filter((r) => Number.isFinite(Number(r.latitude)) && Number.isFinite(Number(r.longitude)))
    .map((r) => [Number(r.latitude), Number(r.longitude)]), [results])

  const cellRings = useMemo(() => {
    const out = {}
    for (const r of results) {
      const p = points.find((pt) => pt.label === r.section_label &&
        Math.abs(pt.lat - Number(r.latitude)) < 1e-9 && Math.abs(pt.lng - Number(r.longitude)) < 1e-9)
      const ring = p ? gridCellRing(polygon, p.cell) : null
      if (ring) out[r.section_label] = ring
    }
    return out
  }, [results, points, polygon])

  if (!analysis) return null
  const fs = analysis.field_summary || {}
  const center = (polygon.length >= 3 && polygonCentroid(polygon)) || positions[0] || START_CENTER
  const crops = [...new Set(results.map((r) => r.crop_type).filter(Boolean))]
  const profile = usableProfile(analysis.soil_profile)
  const hasFieldPanels = !!(analysis.fertilizer_total || analysis.soil_point || analysis.microbial)

  function jumpTo(label) {
    const el = document.getElementById(`region-${label}`)
    if (!el) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
    el.focus({ preventScroll: true })
    setFlash(label)
    setTimeout(() => setFlash(null), 1600)
  }

  async function handleDownload() {
    setDownload({ state: 'working', message: '' })
    try {
      // The report's map is always north up: straighten a turned map for the
      // capture, then turn it back.
      const turned = leafletMap && result.bearing
      if (turned) {
        leafletMap.setBearing(0)
        await new Promise((resolve) => setTimeout(resolve, 400))
      }
      let mapImage
      try {
        mapImage = await captureMapImage(mapRef.current)
      } finally {
        if (turned) leafletMap.setBearing(result.bearing)
      }
      await downloadReport(result.pdfToken, mapImage)
      setDownload({ state: 'done', message: 'Your PDF report is downloading.' })
    } catch (err) {
      setDownload({ state: 'error', message: err.message || 'That download didn’t work. Please try again.' })
    }
  }

  return (
    <section aria-labelledby="results-title" className="step results">
      <header className="rhero">
        <div className="rhero__main">
          <p className="kicker kicker--rule"><span className="kicker__num">04</span> Results</p>
          <h1 id="results-title" className="h1 rhero__title">{analysis.farm_name || 'Your field'}</h1>
          <p className="lead rhero__lead">{headline(fs, results)}</p>
          <div className="rhero__actions">
            <button type="button" className="btn btn--primary btn--lg" onClick={handleDownload} disabled={download.state === 'working'}>
              {download.state === 'working' ? 'Preparing your PDF…' : <>Download the PDF report <Arrow down /></>}
            </button>
            <button type="button" className="btn btn--quiet" onClick={editAgain}>Change readings</button>
          </div>
          <p className={`rhero__status${download.state === 'error' ? ' error-text' : ' help'}`} role="status" aria-live="polite">
            {download.message || 'Map and every region included. The download link works for one hour.'}
          </p>
        </div>
        <ScoreDial score={fs.mean_health_score} category={fs.health_category} />
      </header>

      <dl className="facts-strip">
        <div><dt className="label">Range across the field</dt><dd className="mono">{fmt(fs.min_health_score)}–{fmt(fs.max_health_score)}</dd></div>
        <div><dt className="label">Most common issue</dt><dd>{fs.dominant_problem_code && fs.dominant_problem_code !== 'none' ? fs.dominant_problem_label : 'None found'}</dd></div>
        <div><dt className="label">Regions</dt><dd className="mono">{results.length}</dd></div>
        <div><dt className="label">Crop{crops.length === 1 ? '' : 's'}</dt><dd>{crops.join(', ') || '—'}</dd></div>
      </dl>

      <div className="map map--results" ref={mapRef} role="region"
        aria-label="Results map. Each square is coloured by its soil health score; select a point to jump to its details.">
        {/* No wheel zoom: this page is read by scrolling, and a wheel over
            the map would zoom it instead. Buttons, double-click and pinch
            still zoom. Half-step zoom lets the field fill the frame. */}
        <MapContainer ref={setLeafletMap} center={center} zoom={15} scrollWheelZoom={false} zoomSnap={0.5}
          {...ROTATE_OPTIONS} bearing={result.bearing || 0}>
          <TileLayer url={ESRI_URL} attribution={ESRI_ATTR} maxZoom={19} crossOrigin="anonymous" />
          <MapBearing angle={result.bearing || 0} />
          <InvalidateSizeOnVisible />
          <ScoreLegend />
          <FitView polygon={polygon} positions={positions} />
          {polygon.length >= 3 && (
            <Polygon positions={polygon} pathOptions={{ color: FIELD_BLUE, weight: 2, fillColor: FIELD_BLUE, fillOpacity: 0.06 }} />
          )}
          <Pane name="score-cells" pane="rotatePane" style={{ zIndex: 390 }}>
            {results.map((r) => {
              const ring = cellRings[r.section_label]
              if (!ring) return null
              const color = scoreColor(r.health_score)
              return (
                <Polygon key={`cell-${r.section_label}`} positions={ring}
                  pathOptions={{ color, weight: 1, opacity: 0.7, fillColor: color, fillOpacity: 0.42 }}
                  eventHandlers={{ click: () => jumpTo(r.section_label) }} />
              )
            })}
          </Pane>
          {results.map((r) => {
            const lat = Number(r.latitude)
            const lng = Number(r.longitude)
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
            return (
              <CircleMarker key={r.section_label} center={[lat, lng]} radius={9}
                pathOptions={{ color: '#fff', weight: 2, fillColor: scoreColor(r.health_score), fillOpacity: 1 }}
                eventHandlers={{ click: () => jumpTo(r.section_label) }}>
                <Tooltip permanent direction="top" className="label-tip">
                  {r.section_label} · {fmt(r.health_score)}
                </Tooltip>
              </CircleMarker>
            )
          })}
        </MapContainer>
      </div>

      <nav className="jump" aria-label="Jump to a region">
        <span className="label jump__label">Jump to</span>
        {results.map((r) => (
          <button key={r.section_label} type="button" className="jump__chip"
            style={{ '--score': scoreColor(r.health_score) }} onClick={() => jumpTo(r.section_label)}
            aria-label={`Region ${r.section_label}, score ${fmt(r.health_score)}`}>
            <span className="jump__dot" aria-hidden="true" />
            <span className="mono">{r.section_label}</span>
            <span className="mono jump__score">{fmt(r.health_score)}</span>
          </button>
        ))}
      </nav>

      {hasFieldPanels && (
        <div className="fpanels">
          <FieldFertilizerTotal total={analysis.fertilizer_total} />
          {analysis.soil_point && <SoilProfile point={analysis.soil_point} profile={analysis.soil_profile} />}
          <SoilBiology microbial={analysis.microbial} />
        </div>
      )}

      <div className="regions-head">
        <h2 className="h2">Region by <em>region.</em></h2>
        <p className="help">One card for every square of the grid. Select a square on the map to jump to its card.</p>
      </div>
      <div className="regions">
        {results.map((r) => (
          <RegionCard key={r.section_label} r={r} highlighted={flash === r.section_label}
            profile={profile} microbial={analysis.microbial} />
        ))}
      </div>

      <div className="actions">
        <button type="button" className="btn btn--quiet btn--back" onClick={editAgain}><Arrow back /> Change readings</button>
        <span className="actions__spacer" />
        <button type="button" className="btn btn--ghost" onClick={resetAll}>Start a new field</button>
        <button type="button" className="btn btn--primary" onClick={handleDownload} disabled={download.state === 'working'}>
          {download.state === 'working' ? 'Preparing…' : 'Download PDF'}
        </button>
      </div>
      <p className="disclaimer">
        These results are guidance from soil readings you entered, not a
        substitute for a certified soil lab. For a full test, contact the Texas
        A&amp;M Soil, Water and Forage Testing Lab or your county AgriLife
        Extension agent.
      </p>
    </section>
  )
}
