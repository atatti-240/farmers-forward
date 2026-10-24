/* ==========================================================================
   Farmers Forward - MicrobialInput
   Step 2 side panel section for the optional soil respiration lab value
   (24-hour CO2 burst): one number for the whole field, saved to the draft as
   typed. Blank is fine; anything but a number from 0 to 5000 ppm CO2-C is
   flagged here and not sent.
   ========================================================================== */

import { useId } from 'react'

import { useDashboard } from './DashboardProvider.jsx'
import { RESPIRATION_UNITS, parseRespiration } from './microbial.js'
import './microbial.css'

export default function MicrobialInput() {
  const id = useId()
  const { respiration, setRespiration } = useDashboard()

  const { error } = parseRespiration(respiration)
  const describedBy = [`${id}-unit`, `${id}-hint`, error && `${id}-error`].filter(Boolean).join(' ')

  return (
    <div className="ff-side__section">
      <h3 className="ff-side__title">Soil biology (optional)</h3>
      <div className="ff-field ff-mb-0">
        <label className="ff-label" htmlFor={id}>
          Soil respiration (24h CO₂ burst)
        </label>
        <div className="ff-microbial-entry__row">
          {/* A text box with a decimal keypad, not type="number": a number box
              blanks text it cannot read ("45 ppm" in Safari or Firefox), so the
              draft would lose it and Step 3 could not say it won't be sent. */}
          <input
            id={id}
            className={`ff-input${error ? ' ff-input--error' : ''}`}
            type="text"
            inputMode="decimal"
            autoComplete="off"
            placeholder="e.g. 45"
            value={respiration}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            onChange={(e) => setRespiration(e.target.value)}
          />
          <span id={`${id}-unit`} className="ff-microbial-entry__unit">
            {RESPIRATION_UNITS}
          </span>
        </div>
      </div>
      <p id={`${id}-hint`} className="ff-hint">
        One lab result for the whole field, e.g. a Haney or Solvita CO₂-burst test. Leave
        blank if you don&apos;t have one.
      </p>
      {error && (
        <p id={`${id}-error`} className="ff-error-text ff-microbial-entry__error">
          {error}
        </p>
      )}
    </div>
  )
}
