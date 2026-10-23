/* ==========================================================================
   Farmers Forward (public site) - Step 3: Review + run
   A read-only table of every point. "Run my assessment" sends the field to
   POST /api/public/assessments; the response (the only copy of the results)
   goes straight to the results step.
   ========================================================================== */

import { useState } from 'react'

import { createAssessment } from '../api/client.js'
import { polygonCentroid } from './geo.js'
import { MEASUREMENTS, useTool } from './ToolProvider.jsx'

const HEAD = { ph: 'pH', ec: 'EC', nitrogen: 'N', phosphorus: 'P', potassium: 'K' }

function cell(data, label, key) {
  const v = data[label] && data[label][key]
  return v === undefined || v === null || String(v).trim() === '' ? '—' : v
}

export default function Step3Review() {
  const {
    farmName, date, polygon, grid, points, data, sectionComplete, allComplete, goStep, showResult,
  } = useTool()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const incomplete = points.filter((p) => !sectionComplete(p.label))

  async function run() {
    if (!allComplete || submitting) return
    setError('')
    setSubmitting(true)
    const sections = points.map((p) => {
      const d = data[p.label] || {}
      const row = { section_label: p.label, crop_type: d.crop_type.trim(), latitude: p.lat, longitude: p.lng }
      for (const m of MEASUREMENTS) row[m] = Number(d[m])
      return row
    })
    const center = polygon.length >= 3 ? polygonCentroid(polygon) : null
    try {
      const response = await createAssessment({
        farm_name: farmName.trim() || undefined,
        date,
        sections,
        field: {
          polygon,
          grid: grid || undefined,
          points: points.map((p) => ({ label: p.label, lat: p.lat, lng: p.lng })),
        },
        soil_point: center ? { lat: center[0], lon: center[1] } : undefined,
      })
      showResult(response)
    } catch (err) {
      setError(err.message || 'Something went wrong. Please try again.')
      setSubmitting(false)
    }
  }

  return (
    <section aria-labelledby="step3-title">
      {submitting && (
        <div className="overlay" role="alert" aria-live="assertive">
          <div className="overlay__box">
            <div className="analyzing" aria-hidden="true">
              {MEASUREMENTS.map((m, i) => (
                <span key={m} className="analyzing__chip" style={{ '--i': i }}>{HEAD[m]}</span>
              ))}
              <span className="analyzing__core" />
            </div>
            <p className="overlay__title">Analyzing your soil…</p>
            <p className="help">Scoring every point and matching it to the Texas A&amp;M tables for its crop.</p>
          </div>
        </div>
      )}

      <div className="step-head">
        <p className="eyebrow">Step 3 of 4</p>
        <h1 id="step3-title" className="step-head__title">Check and run</h1>
        <p className="step-head__lead">
          Here’s everything you entered. When it looks right, run the
          assessment. It takes a few seconds.
        </p>
      </div>

      <div className="panel review-meta">
        <div>
          <p className="label">Field</p>
          <p className="review-meta__value">{farmName.trim() || 'My field'}</p>
        </div>
        <div>
          <p className="label">Points</p>
          <p className="review-meta__value mono">{points.length}</p>
        </div>
      </div>

      <div className="table-wrap" role="region" aria-label="Soil readings by point" tabIndex={0}>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Point</th>
              <th scope="col">Crop</th>
              {MEASUREMENTS.map((m) => <th key={m} scope="col">{HEAD[m]}</th>)}
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => {
              const complete = sectionComplete(p.label)
              return (
                <tr key={p.label}>
                  <th scope="row">{p.label}</th>
                  <td>{cell(data, p.label, 'crop_type')}</td>
                  {MEASUREMENTS.map((m) => <td key={m} className="mono">{cell(data, p.label, m)}</td>)}
                  <td>
                    <span className={`status${complete ? ' status--ok' : ' status--todo'}`}>
                      {complete ? 'Ready' : 'Missing'}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {incomplete.length > 0 && (
        <p className="note note--warn">
          {incomplete.length} point{incomplete.length === 1 ? '' : 's'} still need{incomplete.length === 1 ? 's' : ''} a
          crop or readings: {incomplete.map((p) => p.label).join(', ')}.{' '}
          <button type="button" className="btn btn--link" onClick={() => goStep(2)}>Finish them</button>
        </p>
      )}
      {error && <p className="note note--error" role="alert">{error}</p>}

      <div className="actions">
        <button type="button" className="btn btn--secondary" onClick={() => goStep(2)} disabled={submitting}>← Back</button>
        <span className="actions__spacer" />
        <button type="button" className="btn btn--primary btn--lg" onClick={run} disabled={!allComplete || submitting}>
          Run my assessment
        </button>
      </div>
    </section>
  )
}
