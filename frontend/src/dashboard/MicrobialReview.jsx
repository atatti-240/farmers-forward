/* ==========================================================================
   Farmers Forward - MicrobialReview
   One line in the Step 3 field card: the soil respiration value that will be
   sent, that none was entered (it is optional), or that the text entered is
   not a number from 0 to 5000 and will be left out.
   ========================================================================== */

import { useDashboard } from './DashboardProvider.jsx'
import { DECIMAL, MAX_RESPIRATION, RESPIRATION_UNITS, parseRespiration } from './microbial.js'
import './microbial.css'

// Long enough for any real typo; a pasted paragraph is cut short.
const MAX_SHOWN = 40

export default function MicrobialReview() {
  const { respiration } = useDashboard()
  const { value, error } = parseRespiration(respiration)

  if (error) {
    const raw = String(respiration).trim()
    const shown = raw.length > MAX_SHOWN ? `${raw.slice(0, MAX_SHOWN)}…` : raw
    // 6000 or -5 is a number, just not one the bands cover: say which ones are.
    const problem = DECIMAL.test(raw)
      ? `isn’t a number from 0 to ${MAX_RESPIRATION} ${RESPIRATION_UNITS}`
      : 'isn’t a valid number'
    return (
      <p className="ff-error-text ff-microbial-review">
        Soil respiration “{shown}” {problem}, so it won’t be sent. Fix it in step 2 or clear
        it.
      </p>
    )
  }
  if (value === null) {
    return <p className="ff-hint ff-microbial-review">Soil respiration: not entered (optional)</p>
  }
  return (
    <p className="ff-hint ff-microbial-review">
      Soil respiration: <strong>{value} {RESPIRATION_UNITS}</strong>
    </p>
  )
}
