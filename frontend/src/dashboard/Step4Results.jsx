/* ==========================================================================
   Farmers Forward - Step 4: Results (BUILD_CONTRACT §5)
   Renders the analysis: a field-level summary, per-section colour-coded score
   cards with crop viability and recommendations, a satellite map colouring
   each sample point (and its grid cell) on the soil health gradient, and
   PDF download links. Cards use the same colour as their map marker.
   ========================================================================== */

import { useEffect, useRef, useState } from 'react'
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
import { captureMapImage } from './mapCapture.js'
import { InvalidateSizeOnVisible } from './mapView.js'
import ScoreLegend from './ScoreLegend.jsx'
import { scoreColor } from './soilColor.js'
import SoilProfile from './SoilProfile.jsx'

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
  const mapRef = useRef(null)
  // null | 'working' | 'error'. A failed capture is not an error: the report
  // still downloads, just without its map page.
  const [download, setDownload] = useState(null)

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

  /**
   * Capture the map exactly as it is on screen — colours and all, since the
   * cells and pins are already rendered by the time this button can be
   * clicked — then post it up and save the PDF that comes back.
   */
  async function handleDownloadAll() {
    setDownload('working')
    try {
      const mapImage = await captureMapImage(mapRef.current)
      await api.downloadReportAll(id, mapImage)
      setDownload(null)
    } catch {
      setDownload('error')
    }
  }

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

      {/* Results map. The ref is what the PDF's map capture rasterises. */}
      <div className="ff-map" style={{ marginBottom: 24 }} ref={mapRef}>
        <MapContainer center={center} zoom={15} scrollWheelZoom>
          {/* crossOrigin is what lets the PDF's map capture read this canvas
              back: without it the tiles taint it and toDataURL throws. Esri
              serves these tiles with Access-Control-Allow-Origin: *. */}
          <TileLayer
            url={ESRI_URL}
            attribution={ESRI_ATTR}
            maxZoom={19}
            crossOrigin="anonymous"
          />
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

          {/* Grid cells, in a pane under the markers (overlayPane is 400). */}
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

      {/* Mapped soil survey data and soil biology for the field (display only, not scored). */}
      {(analysis.soil_point || analysis.microbial) && (
        <SoilProfile variant="full" point={analysis.soil_point} profile={analysis.soil_profile} microbial={analysis.microbial} />
      )}

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
                {r.score_basis === 'crop' && (
                  <p className="ff-help">
                    Score is for {r.selected_crop?.profile_name?.toLowerCase() || r.crop_type}.
                    {' '}General soil health: <b>{fmtScore(r.general_health_score)}</b> ({r.general_health_category})
                  </p>
                )}
                {r.selected_crop?.basis && <p className="ff-help">{r.selected_crop.basis}</p>}
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
                  {inputs.ece != null && ` (ECe ~${inputs.ece})`}
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
                  // Older saved results have one flat list instead of the two tiers.
                  (r.crop_suitability.ideal
                    ? [['Ideal', r.crop_suitability.ideal],
                       ['Viable with adjustments', r.crop_suitability.viable_with_adjustments || []]]
                    : [[null, viable]]
                  ).filter(([, crops]) => crops.length > 0).map(([heading, crops]) => (
                    <div key={heading || 'viable'}>
                      {heading && <p className="ff-help ff-mb-0">{heading}</p>}
                      <div className="ff-chips">
                        {crops.map((crop) => (
                          <span key={crop} className="ff-chip">
                            {crop}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))
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
        {/* A button, not a download link: the field map has to be captured
            from the live map and posted with the request. */}
        <button
          type="button"
          className="ff-btn ff-btn--primary"
          onClick={handleDownloadAll}
          disabled={download === 'working'}
        >
          {download === 'working'
            ? 'Preparing report…'
            : 'Download all reports'}
        </button>
        {download === 'error' && (
          <p className="ff-help ff-mb-0" role="alert">
            That download didn&apos;t work. Please try again.
          </p>
        )}
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
