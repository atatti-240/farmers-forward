/* ==========================================================================
   Farmers Forward - SoilProfile
   Mapped USDA soil survey data for one point: the soil series, its typical
   organic matter and surface properties, or why there is none. These are
   typical values for the soil type mapped there, not a measurement of the
   field, and they never change the soil health scores.
   `compact` is the few-line preview in the Step 1 side panel; `full` is the
   card on the results page, which ends with the soil biology block.
   ========================================================================== */

import { useId, useState } from 'react'

import MicrobialActivity from './MicrobialActivity.jsx'
import { isFinalProfile, soilKey, useSoilInfo } from './soil.js'
import './soil.css'

const FALLBACK_NOTE =
  'Mapped soil survey data (USDA NRCS SSURGO), not a measurement of this field.'
const LAB_NOTE = 'Send a sample to a soil lab to measure this field’s actual organic matter.'

// Hydrologic soil groups, in plain words (how fast water soaks in).
const HYDRO_HINTS = {
  A: 'high infiltration, low runoff potential',
  B: 'moderate infiltration',
  C: 'slow infiltration',
  D: 'very slow infiltration, high runoff potential',
}

/** A finite number, or `null`. */
function num(v) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** A non-empty trimmed string, or `null`. */
function text(v) {
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/** Plain-English hint for a hydrologic group such as `D` or `C/D`. */
function hydroHint(group) {
  const g = group.toUpperCase()
  if (HYDRO_HINTS[g]) return HYDRO_HINTS[g]
  // Dual groups: the first letter applies once the soil is drained.
  const dual = /^([ABC])\/D$/.exec(g)
  if (dual) return `${HYDRO_HINTS[dual[1]].split(',')[0]} if drained, very slow if not`
  return null
}

/** "Clay: 11% sand, 37% silt, 51% clay", or whichever part is known. */
function surfaceTexture(p) {
  const fractions = [
    [num(p.sand_pct), 'sand'],
    [num(p.silt_pct), 'silt'],
    [num(p.clay_pct), 'clay'],
  ]
    .filter(([v]) => v !== null)
    .map(([v, name]) => `${Math.round(v)}% ${name}`)
    .join(', ')
  const texture = text(p.texture)
  if (texture && fractions) return `${texture}: ${fractions}`
  return texture || fractions || null
}

/** "0–29 cm" for the top layer, or `null`. */
function layerDepth(p) {
  const top = num(p.layer_top_cm)
  const bottom = num(p.layer_bottom_cm)
  return top !== null && bottom !== null ? `${top}–${bottom} cm` : null
}

/** "Survey range 2.0–6.0%, top layer 0–29 cm", or `null`. */
function organicDetail(p) {
  const parts = []
  const low = num(p.organic_matter_low)
  const high = num(p.organic_matter_high)
  if (low !== null && high !== null) parts.push(`survey range ${low.toFixed(1)}–${high.toFixed(1)}%`)
  const layer = layerDepth(p)
  if (layer) parts.push(`top layer ${layer}`)
  if (parts.length === 0) return null
  const line = parts.join(', ')
  return line.charAt(0).toUpperCase() + line.slice(1)
}

/** [label, value] rows for the fields the profile has; empty ones are left out. */
function soilFacts(p, compact) {
  const facts = []
  const series = text(p.series)
  const pct = num(p.component_pct)
  if (series) {
    facts.push(['Soil series', pct !== null ? `${series} (${Math.round(pct)}% of map unit)` : series])
  }
  if (!compact && text(p.map_unit)) facts.push(['Map unit', text(p.map_unit)])
  if (!compact && text(p.survey_area)) facts.push(['Survey area', text(p.survey_area)])
  const texture = surfaceTexture(p)
  if (texture) facts.push(['Surface texture', texture])
  // With no organic matter line the layer depth would otherwise go unsaid.
  if (!compact && num(p.organic_matter_pct) === null && layerDepth(p)) {
    facts.push(['Surface layer', layerDepth(p)])
  }
  if (text(p.drainage)) facts.push(['Drainage', text(p.drainage)])
  const hydro = text(p.hydrologic_group)
  if (!compact && hydro) {
    // The hint describes a soil; a "Water" map unit can still carry a group.
    const hint = p.status === 'ok' ? hydroHint(hydro) : null
    facts.push(['Hydrologic group', hint ? `${hydro}: ${hint}` : hydro])
  }
  const cec = num(p.cec)
  if (!compact && cec !== null) {
    // One decimal, as the PDF prints it ("25.0", not "25").
    facts.push(['CEC (nutrient holding)', `${cec.toFixed(1)} meq/100 g`])
  }
  return facts
}

function Facts({ facts }) {
  if (facts.length === 0) return null
  return (
    <dl className="ff-soil__facts">
      {facts.map(([label, value]) => (
        <div key={label} className="ff-soil__fact">
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Organic matter headline (or why it is missing) and the soil's properties. */
function SoilData({ profile, compact }) {
  const om = num(profile.organic_matter_pct)
  const detail = organicDetail(profile)
  return (
    <>
      {om !== null ? (
        <div className="ff-soil__om">
          <p className="ff-soil__om-line">
            Typical organic matter for this soil type: <strong>{om.toFixed(1)}%</strong>
          </p>
          {detail && <p className="ff-soil__om-detail">{detail}</p>}
        </div>
      ) : (
        // 'partial' explains itself in the notice beside Retry.
        profile.status === 'ok' &&
        text(profile.reason) && <p className="ff-soil__notice">{text(profile.reason)}</p>
      )}
      <Facts facts={soilFacts(profile, compact)} />
    </>
  )
}

/**
 * Soil Profile panel for one point.
 *
 * @param {Object} props
 * @param {{ lat: number, lon: number }|null} props.point - Point to look up.
 * @param {Object|null} [props.profile] - Profile saved with the analysis; a
 *   missing, partial or unavailable one is fetched again.
 * @param {'compact'|'full'} [props.variant='full']
 * @param {Object|null} [props.microbial] - `analysis.microbial`, for the soil
 *   biology block at the end of the full card (see MicrobialActivity).
 */
export default function SoilProfile({ point, profile: savedProfile = null, variant = 'full', microbial = null }) {
  const titleId = useId()
  const compact = variant === 'compact'
  // The full card shows a saved analysis; the Step 1 preview has none.
  const { status, profile, error, retry } = useSoilInfo(point, savedProfile, {
    fromAnalysis: !compact,
  })
  // Point the user last pressed Retry for, so the button stays put (and keeps
  // keyboard focus) while that retry is in flight.
  const [retriedKey, setRetriedKey] = useState(null)

  const key = (profile && text(profile.point_key)) || soilKey(point)
  // No point (so no lookup): the full card still carries the soil biology block.
  const surveyed = !!key || !!profile
  if (!surveyed && compact) return null
  const where = key ? key.replace(',', ', ') : null

  const loading = status === 'loading'
  const kind = profile ? profile.status : null
  const hasSoil = kind === 'ok' || kind === 'partial'
  // 'partial', 'unavailable', a 'no_data' from SoilWeb alone (Soil Data
  // Access did not answer) or a failed request: worth asking again.
  const unsettled = !!profile && !isFinalProfile(profile)
  const retrying = loading && retriedKey !== null && retriedKey === soilKey(point)
  const canRetry = status === 'error' || unsettled || retrying
  let warning = error
  if (!warning && unsettled) {
    // A no_data notice already shows its reason; say why it may change.
    warning =
      kind === 'no_data'
        ? 'The USDA soil survey service didn’t answer, so this may not be the full picture.'
        : text(profile.reason) || 'Soil survey data isn’t available for this point right now.'
  }
  const note = text(profile && profile.source_note) || FALLBACK_NOTE

  function handleRetry() {
    if (loading) return
    setRetriedKey(soilKey(point))
    retry()
  }

  const body = (
    <div className="ff-soil__body" aria-live="polite" aria-busy={loading}>
      {loading && (
        <p className="ff-soil__loading">
          <span className="ff-spinner ff-spinner--sm" aria-hidden="true" />
          Looking up soil survey data…
        </p>
      )}

      {hasSoil && <SoilData profile={profile} compact={compact} />}

      {kind === 'no_data' && (
        <div className="ff-soil__notice">
          <p className="ff-soil__empty-title">No soil survey data for this point</p>
          {text(profile.reason) && <p className="ff-soil__reason">{text(profile.reason)}</p>}
        </div>
      )}
      {kind === 'no_data' && !compact && <Facts facts={soilFacts(profile, false)} />}

      {warning && <p className="ff-soil__notice ff-soil__notice--warn">{warning}</p>}

      {canRetry && (
        <div className="ff-soil__actions">
          {/* aria-disabled, not disabled: a disabled button drops focus. */}
          <button
            type="button"
            className="ff-btn ff-btn--secondary ff-btn--sm"
            onClick={handleRetry}
            aria-disabled={loading}
          >
            {loading ? 'Checking…' : 'Retry'}
          </button>
        </div>
      )}
    </div>
  )

  const caption = (
    <p className="ff-soil__note">
      {note}
      {!compact && hasSoil && ` ${LAB_NOTE}`}
    </p>
  )

  if (compact) {
    return (
      <div className="ff-soil ff-soil--compact">
        {where && <p className="ff-soil__where">At {where}</p>}
        {body}
        {caption}
      </div>
    )
  }

  return (
    <section className="ff-card ff-soil ff-soil--full" aria-labelledby={titleId}>
      <div className="ff-soil__head">
        <h2 id={titleId} className="ff-card__title ff-soil__title">
          Soil Profile
        </h2>
        {where && <span className="ff-soil__where">USDA soil survey at {where}</span>}
      </div>
      {surveyed && body}
      {surveyed && caption}
      <MicrobialActivity microbial={microbial} />
    </section>
  )
}
