/* ==========================================================================
   Farmers Forward (public site) - Step 3: Review + run
   A read-only table of every point. "Run my assessment" sends the field to
   POST /api/public/assessments; the response (the only copy of the results)
   goes straight to the results step.
   ========================================================================== */

import { useState } from 'react'

import { createAssessment } from '../api/client.js'
import Arrow from '../components/Arrow.jsx'
import { polygonCentroid } from './geo.js'
import StepHead from './StepHead.jsx'
import { MEASUREMENTS, useTool } from './ToolProvider.jsx'

const HEAD = { ph: 'pH', ec: 'EC', nitrogen: 'N', phosphorus: 'P', potassium: 'K' }
const UNIT = { ph: '', ec: 'dS/m', nitrogen: 'ppm', phosphorus: 'ppm', potassium: 'ppm' }

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

  const crops = [...new Set(points.map((p) => (data[p.label]?.crop_type || '').trim()).filter(Boolean))]

  return (
    <section className="step" aria-labelledby="step3-title">
      {submitting && (
        <div className="analyzing" role="alert" aria-live="assertive">
          <div className="analyzing__inner">
            <p className="kicker kicker--rule"><span className="kicker__num">●</span> Running</p>
            <p className="h2 analyzing__title">Reading the <em>soil.</em></p>
            <ol className="analyzing__steps" aria-hidden="true">
              {MEASUREMENTS.map((m, i) => (
                <li key={m} className="analyzing__step mono" style={{ '--i': i }}>{HEAD[m]}</li>
              ))}
            </ol>
            <div className="analyzing__track" aria-hidden="true"><span /></div>
            <p className="help">
              Scoring {points.length} point{points.length === 1 ? '' : 's'} and matching each to the
              Texas A&amp;M soil-test tables for its crop.
            </p>
          </div>
        </div>
      )}

      <StepHead id="step3-title" num="03" kicker="Readings · check" title={<>Check, then <em>run.</em></>}>
        Everything you entered, in one place. When it looks right, run the
        assessment. It takes a few seconds.
      </StepHead>

      <dl className="facts-strip">
        <div><dt className="label">Field</dt><dd>{farmName.trim() || 'My field'}</dd></div>
        <div><dt className="label">Sample points</dt><dd className="mono">{points.length}</dd></div>
        <div><dt className="label">Crop{crops.length === 1 ? '' : 's'}</dt><dd>{crops.length ? crops.join(', ') : '—'}</dd></div>
        <div><dt className="label">Ready</dt><dd className="mono">{points.length - incomplete.length} / {points.length}</dd></div>
      </dl>

      <div className="table-wrap" role="region" aria-label="Soil readings by point" tabIndex={0}>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Point</th>
              <th scope="col">Crop</th>
              {MEASUREMENTS.map((m) => (
                <th key={m} scope="col" className="num">
                  {HEAD[m]}{UNIT[m] && <span className="table__unit"> {UNIT[m]}</span>}
                </th>
              ))}
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => {
              const complete = sectionComplete(p.label)
              return (
                <tr key={p.label}>
                  <th scope="row" className="mono">{p.label}</th>
                  <td>{cell(data, p.label, 'crop_type')}</td>
                  {MEASUREMENTS.map((m) => <td key={m} className="mono num">{cell(data, p.label, m)}</td>)}
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
          crop or readings: <span className="mono">{incomplete.map((p) => p.label).join(', ')}</span>.{' '}
          <button type="button" className="btn btn--link" onClick={() => goStep(2)}>Finish them</button>
        </p>
      )}
      {error && <p className="note note--error" role="alert">{error}</p>}

      <div className="actions">
        <button type="button" className="btn btn--quiet btn--back" onClick={() => goStep(2)} disabled={submitting}><Arrow back /> Readings</button>
        <span className="actions__spacer" />
        <button type="button" className="btn btn--primary btn--lg" onClick={run} disabled={!allComplete || submitting}>
          Run my assessment <Arrow />
        </button>
      </div>
    </section>
  )
}
