/* ==========================================================================
   Farmers Forward (public site) - Step 4: Results
   Renders the POST /assessments response (kept in memory only): a field
   summary, the satellite map with every grid square coloured by soil health,
   and one plain-English card per region, plus the PDF download. The PDF is
   built server-side from the stored submission using the 1-hour token from
   that same response; the map is rasterised here and sent along.
   Green/amber/red appear ONLY on data (map squares, dots, score badges).
   ========================================================================== */

import { useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, Pane, TileLayer, Polygon, CircleMarker, Tooltip, useMap } from 'react-leaflet'

import { downloadReport } from '../api/client.js'
import { gridCellRing, polygonCentroid } from './geo.js'
import { captureMapImage } from './mapCapture.js'
import { ESRI_ATTR, ESRI_URL, FIELD_BLUE, START_CENTER } from './mapConfig.js'
import { InvalidateSizeOnVisible } from './mapView.js'
import { FertilizerPlan, FieldFertilizerTotal, SoilProfile } from './OptionalBlocks.jsx'
import ScoreLegend from './ScoreLegend.jsx'
import { scoreColor } from './soilColor.js'
import { useTool } from './ToolProvider.jsx'

const fmt = (v) => (Number.isFinite(Number(v)) ? Math.round(Number(v)) : '—')

const NUTRIENT_NAMES = { N: 'Nitrogen (N)', P: 'Phosphate (P₂O₅)', K: 'Potash (K₂O)' }

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
        map.fitBounds(pts, { padding: [36, 36], maxZoom: 18 })
        done.current = true
      } catch { /* ignore */ }
    }
    attempt()
    return () => cancelAnimationFrame(frame)
  }, [map, polygon, positions])
  return null
}

function ScoreBadge({ score, category, size = 'md' }) {
  return (
    <span className={`score score--${size}`} style={{ '--score': scoreColor(score) }}>
      <span className="score__num mono">{fmt(score)}</span>
      {category && <span className="score__cat">{category}</span>}
    </span>
  )
}

function NutrientNeed({ assessment }) {
  if (!assessment || typeof assessment !== 'object') return null
  const rows = ['N', 'P', 'K']
    .map((k) => [k, assessment[k], assessment.units && assessment.units[k]])
    .filter(([, v]) => v && Number.isFinite(Number(v.add)))
  if (rows.length === 0) return null
  return (
    <div className="block">
      <h4 className="block__title">Texas A&amp;M nutrient need</h4>
      <dl className="need">
        {rows.map(([k, v, unit]) => (
          <div key={k} className="need__row">
            <dt>{NUTRIENT_NAMES[k]}</dt>
            <dd>
              {Number(v.add) > 0
                ? <><b className="mono">{Math.round(Number(v.add))}</b> {unit || 'lbs/acre'}</>
                : 'None needed'}
            </dd>
          </div>
        ))}
      </dl>
      {assessment.crop && <p className="source">From the Texas A&amp;M soil-test table for {String(assessment.crop).toLowerCase()}.</p>}
    </div>
  )
}

function RegionCard({ r, highlighted }) {
  const inputs = r.inputs || {}
  const recs = Array.isArray(r.recommendations) ? r.recommendations : []
  const suit = r.crop_suitability || {}
  const ideal = suit.ideal || []
  const adjust = suit.viable_with_adjustments || []
  const problem = r.primary_problem || {}
  const sc = r.selected_crop || {}
  const noProblem = !problem.code || problem.code === 'none'

  return (
    <article
      id={`region-${r.section_label}`}
      className={`region${highlighted ? ' region--flash' : ''}`}
      style={{ '--score': scoreColor(r.health_score) }}
      aria-labelledby={`region-${r.section_label}-title`}
      tabIndex={-1}
    >
      <header className="region__head">
        <div>
          <h3 id={`region-${r.section_label}-title`} className="region__title">Region {r.section_label}</h3>
          <p className="region__crop">Growing {r.crop_type || 'an unrecorded crop'}</p>
        </div>
        <ScoreBadge score={r.health_score} category={r.health_category} />
      </header>

      <ul className="readings" aria-label="Readings entered">
        <li><span>pH</span><b className="mono">{inputs.ph ?? '—'}</b></li>
        <li><span>EC</span><b className="mono">{inputs.ec ?? '—'}</b></li>
        <li><span>N</span><b className="mono">{inputs.nitrogen ?? '—'}</b></li>
        <li><span>P</span><b className="mono">{inputs.phosphorus ?? '—'}</b></li>
        <li><span>K</span><b className="mono">{inputs.potassium ?? '—'}</b></li>
      </ul>

      <div className="block">
        <h4 className="block__title">What we found</h4>
        {noProblem
          ? <p>No major problems for this crop. Keep doing what you’re doing.</p>
          : <p><b>{problem.label}.</b> {problem.message}</p>}
        {sc.note && <p className="help">{sc.note}</p>}
      </div>

      {recs.length > 0 && (
        <div className="block">
          <h4 className="block__title">What to do</h4>
          <ol className="recs">
            {recs.map((rec, i) => (
              <li key={rec.fix_code || i} className="recs__item">
                <p className="recs__title">{rec.title || 'Action'}</p>
                {rec.action && <p>{rec.action}</p>}
                <dl className="recs__meta">
                  {rec.rate && <div><dt>How much</dt><dd>{rec.rate}</dd></div>}
                  {rec.timing && <div><dt>When</dt><dd>{rec.timing}</dd></div>}
                  {rec.cost_estimate && <div><dt>Rough cost</dt><dd>{rec.cost_estimate}</dd></div>}
                </dl>
              </li>
            ))}
          </ol>
        </div>
      )}

      <NutrientNeed assessment={r.crop_assessment} />
      <FertilizerPlan plan={r.fertilizer_plan} />

      <div className="block">
        <h4 className="block__title">Crops that fit this soil now</h4>
        {ideal.length + adjust.length === 0 ? (
          <p className="help">None of the crops we check fit these readings yet. Start with the fixes above.</p>
        ) : (
          <>
            {ideal.length > 0 && (
              <div className="crop-row">
                <span className="crop-row__label">Best fit</span>
                <span className="tags">{ideal.map((c) => <span key={c} className="tag tag--strong">{c}</span>)}</span>
              </div>
            )}
            {adjust.length > 0 && (
              <div className="crop-row">
                <span className="crop-row__label">With some fixes</span>
                <span className="tags">{adjust.map((c) => <span key={c} className="tag">{c}</span>)}</span>
              </div>
            )}
          </>
        )}
      </div>

      <details className="more">
        <summary>More detail</summary>
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
      </details>
    </article>
  )
}

export default function Step4Results() {
  const { result, resetAll, editAgain } = useTool()
  const mapRef = useRef(null)
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
      const mapImage = await captureMapImage(mapRef.current)
      await downloadReport(result.pdfToken, mapImage)
      setDownload({ state: 'done', message: 'Your PDF report is downloading.' })
    } catch (err) {
      setDownload({ state: 'error', message: err.message || 'That download didn’t work. Please try again.' })
    }
  }

  return (
    <section aria-labelledby="results-title" className="results">
      <div className="step-head">
        <p className="eyebrow">Step 4 of 4 · Results</p>
        <h1 id="results-title" className="step-head__title">{analysis.farm_name || 'Your field'}</h1>
        <p className="step-head__lead">{headline(fs, results)}</p>
      </div>

      <div className="summary">
        <div className="summary__score">
          <ScoreBadge score={fs.mean_health_score} size="lg" />
          <div>
            <p className="summary__cat">{fs.health_category || '—'}</p>
            <p className="help">Average soil health, 0–100</p>
          </div>
        </div>
        <dl className="summary__facts">
          <div><dt>Range across the field</dt><dd className="mono">{fmt(fs.min_health_score)}–{fmt(fs.max_health_score)}</dd></div>
          <div><dt>Most common issue</dt><dd>{fs.dominant_problem_code && fs.dominant_problem_code !== 'none' ? fs.dominant_problem_label : 'None found'}</dd></div>
          <div><dt>Regions analyzed</dt><dd className="mono">{results.length}</dd></div>
        </dl>
        <div className="summary__actions">
          <button type="button" className="btn btn--primary btn--lg" onClick={handleDownload} disabled={download.state === 'working'}>
            {download.state === 'working' ? 'Preparing your PDF…' : 'Download PDF report'}
          </button>
          <p className={`help${download.state === 'error' ? ' error-text' : ''}`} role="status" aria-live="polite">
            {download.message || 'Includes the map and every region. Available for one hour.'}
          </p>
        </div>
      </div>

      <div className="map map--results" ref={mapRef} role="region"
        aria-label="Results map. Each square is coloured by its soil health score; select a point to jump to its details.">
        {/* No wheel zoom: this page is read by scrolling, and a wheel over
            the map would zoom it instead. Buttons, double-click and pinch
            still zoom. Half-step zoom lets the field fill the frame. */}
        <MapContainer center={center} zoom={15} scrollWheelZoom={false} zoomSnap={0.5}>
          <TileLayer url={ESRI_URL} attribution={ESRI_ATTR} maxZoom={19} crossOrigin="anonymous" />
          <InvalidateSizeOnVisible />
          <ScoreLegend />
          <FitView polygon={polygon} positions={positions} />
          {polygon.length >= 3 && (
            <Polygon positions={polygon} pathOptions={{ color: FIELD_BLUE, weight: 2, fillColor: FIELD_BLUE, fillOpacity: 0.06 }} />
          )}
          <Pane name="score-cells" style={{ zIndex: 390 }}>
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

      <nav className="region-index" aria-label="Jump to a region">
        {results.map((r) => (
          <button key={r.section_label} type="button" className="region-index__chip"
            style={{ '--score': scoreColor(r.health_score) }} onClick={() => jumpTo(r.section_label)}>
            <span className="region-index__dot" aria-hidden="true" />
            {r.section_label} <span className="mono">{fmt(r.health_score)}</span>
          </button>
        ))}
      </nav>

      <FieldFertilizerTotal total={analysis.fertilizer_total} />
      {analysis.soil_point && <SoilProfile point={analysis.soil_point} profile={analysis.soil_profile} />}

      <h2 className="section-title section-title--spaced">Region by region</h2>
      <div className="regions">
        {results.map((r) => <RegionCard key={r.section_label} r={r} highlighted={flash === r.section_label} />)}
      </div>

      <div className="actions">
        <button type="button" className="btn btn--secondary" onClick={editAgain}>← Change readings</button>
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
