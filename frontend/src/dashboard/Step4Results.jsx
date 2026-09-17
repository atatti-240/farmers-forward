/* ==========================================================================
   Farmers Forward - Step 4: Results (BUILD_CONTRACT §5)
   Renders the analysis: a field-level summary, per-section colour-coded score
   cards with crop viability and recommendations, a satellite map colouring
   each sample point (and its auto-grid cell) on the soil health gradient, and
   PDF download links. Cards use the same colour as their map marker.
   ========================================================================== */

import { useEffect, useRef } from 'react'
import {
  MapContainer,
  Pane,
  TileLayer,
  Polygon,
  CircleMarker,
  Tooltip,
  useMap,
} from 'react-leaflet'

import api from '../api/client.js'
import { useDashboard } from './DashboardProvider.jsx'
import { gridCellRing, polygonCentroid } from './geo.js'
import { InvalidateSizeOnVisible } from './mapView.js'
import ScoreLegend from './ScoreLegend.jsx'
import { scoreColor } from './soilColor.js'

const ESRI_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const ESRI_ATTR = 'Tiles © Esri'
const CENTER = [29.53, -95.77]
const BLUE = '#4AAFE0'

function fmtScore(v) {
  const n = Number(v)
  return Number.isFinite(n) ? Math.round(n) : 'N/A'
}

/** Fit the results map to the sample points (or polygon) once on mount. */
function FitView({ polygon, positions }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    if (done.current) return
    const pts = positions.length >= 2 ? positions : polygon
    if (pts && pts.length >= 2) {
      try {
        map.fitBounds(pts, { padding: [30, 30], maxZoom: 17 })
        done.current = true
      } catch {
        /* ignore */
      }
    }
  }, [map, polygon, positions])
  return null
}

export default function Step4Results() {
  const { analysis, farmId, polygon, points, resetDraft } = useDashboard()

  if (!analysis || !Array.isArray(analysis.results)) {
    return (
      <div>
        <div className="ff-step-head">
          <h1>No results yet</h1>
          <p>Submit a field to see its analysis here.</p>
        </div>
        <button
          type="button"
          className="ff-btn ff-btn--primary"
          onClick={resetDraft}
        >
          Start a new field
        </button>
      </div>
    )
  }

  const fs = analysis.field_summary || {}
  const results = analysis.results
  const id = analysis.farm_id != null ? analysis.farm_id : farmId

  const positions = results
    .filter((r) => Number.isFinite(Number(r.latitude)) && Number.isFinite(Number(r.longitude)))
    .map((r) => [Number(r.latitude), Number(r.longitude)])

  // Grid cell for each result, taken from the submitted point with the same
  // label and position (so a grid regenerated after submitting isn't used).
  const cellRings = {}
  for (const r of results) {
    const p = (points || []).find(
      (pt) =>
        pt.label === r.section_label &&
        Math.abs(pt.lat - Number(r.latitude)) < 1e-9 &&
        Math.abs(pt.lng - Number(r.longitude)) < 1e-9
    )
    const ring = p ? gridCellRing(polygon, p.cell) : null
    if (ring) cellRings[r.section_label] = ring
  }

  const center =
    (polygon && polygon.length >= 3 && polygonCentroid(polygon)) ||
    (positions.length > 0 ? positions[0] : CENTER)

  return (
    <div>
      <div className="ff-step-head">
        <h1>{analysis.farm_name || 'Field results'}</h1>
        <p>
          Analyzed {analysis.date || ''} · {fs.n_sections != null ? fs.n_sections : results.length}{' '}
          section{(fs.n_sections != null ? fs.n_sections : results.length) === 1 ? '' : 's'}
        </p>
      </div>

      {/* Field-level summary */}
      <div className="ff-card" style={{ marginBottom: 24 }}>
        <div className="ff-summary" style={{ marginBottom: 0 }}>
          <div
            className="ff-summary__score"
            style={{ '--score-color': scoreColor(fs.mean_health_score) }}
          >
            <span className="num">{fmtScore(fs.mean_health_score)}</span>
            <span className="cap">{fs.health_category || 'Score'}</span>
          </div>
          <dl className="ff-summary__meta">
            <dt>Health range</dt>
            <dd>
              {fmtScore(fs.min_health_score)} – {fmtScore(fs.max_health_score)}
            </dd>
            <dt>Dominant issue</dt>
            <dd>{fs.dominant_problem_label || 'None detected'}</dd>
            <dt>Sections analyzed</dt>
            <dd>{fs.n_sections != null ? fs.n_sections : results.length}</dd>
          </dl>
        </div>
      </div>

      {/* Results map */}
      <div className="ff-map" style={{ marginBottom: 24 }}>
        <MapContainer center={center} zoom={15} scrollWheelZoom>
          <TileLayer url={ESRI_URL} attribution={ESRI_ATTR} maxZoom={19} />
          <InvalidateSizeOnVisible />
          <ScoreLegend />
          <FitView polygon={polygon} positions={positions} />

          {polygon && polygon.length >= 3 && (
            <Polygon
              positions={polygon}
              pathOptions={{
                color: BLUE,
                weight: 2,
                fillColor: BLUE,
                fillOpacity: 0.08,
              }}
            />
          )}

          {/* Auto-grid cells, in a pane under the markers (overlayPane is 400). */}
          <Pane name="ff-cells" style={{ zIndex: 390 }}>
            {results.map((r) => {
              const ring = cellRings[r.section_label]
              if (!ring) return null
              const color = scoreColor(r.health_score)
              return (
                <Polygon
                  key={`cell-${r.section_label}`}
                  positions={ring}
                  pathOptions={{
                    color,
                    weight: 1,
                    opacity: 0.6,
                    fillColor: color,
                    fillOpacity: 0.38,
                  }}
                />
              )
            })}
          </Pane>

          {results.map((r) => {
            const hex = scoreColor(r.health_score)
            const lat = Number(r.latitude)
            const lng = Number(r.longitude)
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
            return (
              <CircleMarker
                key={r.section_label}
                center={[lat, lng]}
                radius={9}
                pathOptions={{
                  color: '#fff',
                  weight: 2,
                  fillColor: hex,
                  fillOpacity: 1,
                }}
              >
                <Tooltip permanent direction="top" className="ff-label-tip">
                  {r.section_label} · {fmtScore(r.health_score)}
                </Tooltip>
              </CircleMarker>
            )
          })}
        </MapContainer>
      </div>

      {/* Per-section cards */}
      <div className="ff-results-grid">
        {results.map((r) => {
          const hex = scoreColor(r.health_score)
          const inputs = r.inputs || {}
          const viable =
            (r.crop_suitability && r.crop_suitability.viable_now) || []
          const recs = Array.isArray(r.recommendations) ? r.recommendations : []
          return (
            <div
              key={r.section_label}
              className="ff-card ff-result-card"
              style={{ '--score-color': hex }}
            >
              <div className="ff-result-card__head">
                <span className="ff-result-card__name">
                  <span className="ff-result-card__dot" aria-hidden="true" />
                  <span className="ff-result-card__label">
                    {r.section_label}
                  </span>
                </span>
                <span className="ff-score">
                  <span className="ff-score__num">
                    {fmtScore(r.health_score)}
                  </span>
                  <span className="ff-score__cat">
                    {r.health_category || ''}
                  </span>
                </span>
              </div>

              <div className="ff-crop-context">
                <p className="ff-crop-context__name">Growing: {r.crop_type || 'Not recorded'}</p>
                {r.selected_crop?.note && <p className="ff-help">{r.selected_crop.note}</p>}
                {r.selected_crop?.limiting_factors?.length > 0 && (
                  <ul>{r.selected_crop.limiting_factors.map((factor) => <li key={factor}>{factor}</li>)}</ul>
                )}
              </div>

              <div className="ff-inputs-line">
                <span>
                  <b>pH</b> {inputs.ph ?? 'N/A'}
                </span>
                <span>
                  <b>EC</b> {inputs.ec ?? 'N/A'}
                </span>
                <span>
                  <b>N</b> {inputs.nitrogen ?? 'N/A'}
                </span>
                <span>
                  <b>P</b> {inputs.phosphorus ?? 'N/A'}
                </span>
                <span>
                  <b>K</b> {inputs.potassium ?? 'N/A'}
                </span>
              </div>

              {r.primary_problem && r.primary_problem.label && (
                <div>
                  <p className="ff-result-sub">Primary concern</p>
                  <p className="ff-mb-0">
                    {r.primary_problem.label}
                    {r.primary_problem.message
                      ? `: ${r.primary_problem.message}`
                      : ''}
                  </p>
                </div>
              )}

              <div>
                <p className="ff-result-sub">Crops viable now</p>
                {viable.length > 0 ? (
                  <div className="ff-chips">
                    {viable.map((crop) => (
                      <span key={crop} className="ff-chip">
                        {crop}
                      </span>
                    ))}
                  </div>
                ) : (
                  <div className="ff-chips">
                    <span className="ff-chip ff-chip--muted">
                      None currently viable
                    </span>
                  </div>
                )}
              </div>

              <div>
                <p className="ff-result-sub">{r.crop_type ? `Recommendations for ${r.crop_type}` : 'Recommendations'}</p>
                {recs.length > 0 ? (
                  <div className="ff-recs">
                    {recs.map((rec, i) => (
                      <div key={rec.fix_code || rec.title || i} className="ff-rec">
                        <p className="ff-rec__title">
                          {rec.title || 'Action item'}
                        </p>
                        {rec.action && (
                          <p className="ff-rec__action">{rec.action}</p>
                        )}
                        {rec.notes && <p className="ff-help ff-rec__notes">{rec.notes}</p>}
                        <div className="ff-rec__meta">
                          {rec.rate && (
                            <span>
                              <b>Rate:</b> {rec.rate}
                            </span>
                          )}
                          {(rec.cost_estimate || rec.cost_estimate === 0) && (
                            <span>
                              <b>Cost:</b> {rec.cost_estimate}
                            </span>
                          )}
                          {rec.timing && (
                            <span>
                              <b>Timing:</b> {rec.timing}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="ff-help ff-mb-0">
                    No general soil corrections flagged. Check the crop assessment above for any crop-specific limits.
                  </p>
                )}
              </div>

              <div className="ff-result-card__foot">
                <a
                  className="ff-btn ff-btn--secondary ff-btn--sm ff-btn--block"
                  href={api.reportSectionUrl(id, r.section_label)}
                  download
                >
                  Download {r.section_label} report
                </a>
              </div>
            </div>
          )
        })}
      </div>

      {/* Field-level actions */}
      <div className="ff-actions">
        <a
          className="ff-btn ff-btn--primary"
          href={api.reportAllUrl(id)}
          download
        >
          Download all reports
        </a>
        <span className="ff-spacer" />
        <button
          type="button"
          className="ff-btn ff-btn--secondary"
          onClick={resetDraft}
        >
          Start new field
        </button>
      </div>
    </div>
  )
}
