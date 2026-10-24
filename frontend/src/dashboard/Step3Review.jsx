/* ==========================================================================
   Farmers Forward - Step 3: Review + Submit (BUILD_CONTRACT §5)
   A read-only table of every section and its five values. Submitting is
   enabled only when all sections are complete; on submit the payload is sent
   to the backend, the result is stored, the draft is cleared, and the flow
   advances to the results step.
   ========================================================================== */

import { useState } from 'react'

import api from '../api/client.js'
import Spinner from '../components/Spinner.jsx'
import { useDashboard } from './DashboardProvider.jsx'
import { sampleAcres } from './fieldArea.js'
import MicrobialReview from './MicrobialReview.jsx'
import { parseRespiration } from './microbial.js'
import { soilPointFor } from './soil.js'

const FIELDS = ['ph', 'ec', 'nitrogen', 'phosphorus', 'potassium']

/** Show the stored value or an em-dash placeholder. */
function cell(data, label, key) {
  const v = data[label] && data[label][key]
  if (v === undefined || v === null || String(v).trim() === '') return 'N/A'
  return v
}

export default function Step3Review() {
  const {
    farmName,
    date,
    points,
    polygon,
    finderPins,
    data,
    respiration,
    sectionComplete,
    allComplete,
    submitSuccess,
    goStep,
  } = useDashboard()

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const nameMissing = !farmName.trim()
  const incomplete = points.filter((p) => !sectionComplete(p.label))
  const canSubmit = allComplete && !nameMissing && !submitting

  async function handleSubmit() {
    if (!canSubmit) return
    setError('')
    setSubmitting(true)

    // Each sample's share of the field, for the fertilizer plan's totals.
    const acres = sampleAcres(polygon, points)
    const sections = points.map((p) => {
      const d = data[p.label] || {}
      return {
        section_label: p.label,
        crop_type: d.crop_type.trim(),
        latitude: p.lat,
        longitude: p.lng,
        ph: Number(d.ph),
        ec: Number(d.ec),
        nitrogen: Number(d.nitrogen),
        phosphorus: Number(d.phosphorus),
        potassium: Number(d.potassium),
        area_acres: acres[p.label] ?? null,
      }
    })

    try {
      const analysis = await api.submitField({
        farm_name: farmName.trim(),
        date,
        sections,
        soil_point: soilPointFor({ polygon, finderPins, points }),
        soil_respiration: parseRespiration(respiration).value,
      })
      submitSuccess(analysis, analysis.farm_id)
      // Component unmounts as the flow advances to step 4.
    } catch (err) {
      const msg =
        (err && err.payload && err.payload.error) ||
        (err && err.message) ||
        'Submission failed. Please try again.'
      setError(msg)
      setSubmitting(false)
    }
  }

  return (
    <div>
      {submitting && (
        <div className="ff-overlay" role="alert" aria-live="assertive">
          <div className="ff-overlay__box">
            <Spinner size="lg" />
            <p>Analyzing soil and generating recommendations…</p>
          </div>
        </div>
      )}

      <div className="ff-step-head">
        <h1>Review &amp; submit</h1>
        <p>
          Check every section before submitting. Once submitted, the field is
          analyzed and saved to the database.
        </p>
      </div>

      <div className="ff-card" style={{ marginBottom: 20 }}>
        <div className="ff-inline ff-inline--between" style={{ marginBottom: 4 }}>
          <div>
            <div className="ff-toolbar__label">Field</div>
            <div style={{ fontWeight: 700, fontSize: '1.1rem' }}>
              {farmName.trim() || <span className="ff-text-muted">Unnamed field</span>}
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="ff-toolbar__label">Date</div>
            <div style={{ fontWeight: 700 }}>{date}</div>
          </div>
        </div>
        <MicrobialReview />
      </div>

      <div
        className="ff-table-wrap ff-card"
        role="region"
        aria-label="Soil measurements by section"
        tabIndex={0}
        style={{ padding: 0, overflow: 'auto' }}
      >
        <table className="ff-table">
          <thead>
            <tr>
              <th>Section</th>
              <th>Crop</th>
              <th>Latitude</th>
              <th>Longitude</th>
              <th>pH</th>
              <th>EC</th>
              <th>N</th>
              <th>P</th>
              <th>K</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {points.length === 0 ? (
              <tr>
                <td colSpan={10} className="ff-text-center ff-text-muted">
                  No sample points. Go back to step 1.
                </td>
              </tr>
            ) : (
              points.map((p) => {
                const complete = sectionComplete(p.label)
                return (
                  <tr key={p.label}>
                    <td style={{ fontWeight: 700 }}>{p.label}</td>
                    <td className="ff-table__crop">{cell(data, p.label, 'crop_type')}</td>
                    <td className="ff-coords">{p.lat.toFixed(6)}</td>
                    <td className="ff-coords">{p.lng.toFixed(6)}</td>
                    {FIELDS.map((f) => (
                      <td key={f}>{cell(data, p.label, f)}</td>
                    ))}
                    <td>
                      <span
                        className={`ff-badge ${complete ? 'ff-badge--green' : 'ff-badge--red'}`}
                      >
                        {complete ? 'Complete' : 'Incomplete'}
                      </span>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {nameMissing && points.length > 0 && (
        <p className="ff-error-text" style={{ marginTop: 14 }}>
          Add a field name in step 1 before submitting.
        </p>
      )}
      {incomplete.length > 0 && (
        <p className="ff-help" style={{ marginTop: 14 }}>
          {incomplete.length} section
          {incomplete.length === 1 ? '' : 's'} still incomplete:{' '}
          {incomplete.map((p) => p.label).join(', ')}. Each section needs a crop and all five soil values.
        </p>
      )}
      {error && (
        <p className="ff-error-text" style={{ marginTop: 14 }}>
          {error}
        </p>
      )}

      <div className="ff-actions">
        <button
          type="button"
          className="ff-btn ff-btn--secondary"
          onClick={() => goStep(2)}
          disabled={submitting}
        >
          Back
        </button>
        <span className="ff-spacer" />
        <button
          type="button"
          className="ff-btn ff-btn--primary"
          onClick={handleSubmit}
          disabled={!canSubmit}
        >
          Submit to database
        </button>
      </div>
    </div>
  )
}
