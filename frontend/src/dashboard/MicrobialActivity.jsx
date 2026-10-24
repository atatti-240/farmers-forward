/* ==========================================================================
   Farmers Forward - MicrobialActivity
   The "Soil biology" block at the end of the results Soil Profile card,
   from `analysis.microbial`: the soil respiration value with its rating band
   and the source of the bands, a note that the rating is unavailable, or,
   when no value was entered, that the in-house test is still in development.
   Display only; it never changes the soil health scores.
   ========================================================================== */

import { NOT_PROVIDED_MESSAGE, RESPIRATION_UNITS } from './microbial.js'
import './microbial.css'

const READING_LABEL = 'Microbial activity (soil respiration, 24-hour CO₂ burst)'
const UNRATED_MESSAGE = 'Microbial activity rating is unavailable right now.'
const GUIDELINE_NOTE = 'A published lab guideline, not calibrated for Texas soils.'
const PLACEHOLDER_WARNING = 'PLACEHOLDER thresholds: not from a published source.'

/** A finite number, or `null`. */
function num(v) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** A non-empty trimmed string, or `null`. */
function text(v) {
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/** "31-50" as "31–50"; "above 200" is left alone. */
function dashed(range) {
  return range.replace(/(\d)\s*-\s*(\d)/g, '$1–$2')
}

/** Bands the scale can draw: `{ label, min, max }` with a numeric `min`. */
function scaleBands(bands) {
  if (!Array.isArray(bands)) return []
  return bands.filter((b) => b && num(b.min) !== null && (b.max === null || num(b.max) !== null))
}

/** "0–10" for a band, or ">200" for the open-ended top one. */
function stepText(band, previous) {
  if (band.max === null) return `>${previous ? previous.max : band.min}`
  return `${band.min}–${band.max}`
}

/** 0-based position of the matched band, or `null`. */
function matchedIndex(m, bands) {
  if (Number.isInteger(m.band_index) && m.band_index >= 0) return m.band_index
  const i = bands.findIndex((b) => text(b.label) === text(m.band))
  return i >= 0 ? i : null
}

/** "Rating bands: <title>, <publisher>, <year>." with the title linked. */
function Citation({ source, placeholder }) {
  const src = source && typeof source === 'object' ? source : {}
  const title = text(src.title)
  const url = text(src.url)
  // Only a web link; anything else (a "javascript:" URL) stays plain text.
  const href = url && /^https?:\/\//i.test(url) ? url : null
  const rest = [text(src.publisher), num(src.year) !== null ? String(src.year) : text(src.year)]
    .filter(Boolean)
    .join(', ')
  if (placeholder) {
    // Placeholder bands are not that guideline: name it, but do not cite it.
    const named = [title, rest].filter(Boolean).join(', ')
    return (
      <p className="ff-microbial__note">
        Rating bands: placeholder values{named ? `, not taken from ${named}` : ''}.
      </p>
    )
  }
  return (
    <p className="ff-microbial__note">
      {title && (
        <>
          Rating bands:{' '}
          {href ? (
            <a href={href} target="_blank" rel="noreferrer">
              {title}
            </a>
          ) : (
            title
          )}
          {rest ? `, ${rest}.` : '.'}{' '}
        </>
      )}
      {GUIDELINE_NOTE}
    </p>
  )
}

/** The rated value, its band, the eight-step scale and where the bands come from. */
function Rated({ m, value }) {
  const bands = scaleBands(m.bands)
  const index = matchedIndex(m, bands)
  const count = Number.isInteger(m.band_count) && m.band_count > 0 ? m.band_count : bands.length
  // The published ranges are whole numbers, so 10.1 is past Very Low's 10 but
  // short of Low's 11 and is counted as Low; "11–20" beside it would seem to
  // leave it out, so a value in such a gap reads from the band below's max.
  const below = index !== null && index > 0 ? bands[index - 1] : null
  const inGap =
    below && num(below.max) !== null && num(m.band_min) !== null && num(m.band_max) !== null &&
    value < m.band_min
  const range = inGap ? `above ${below.max} to ${m.band_max}` : text(m.range_text)
  const where = [
    range && `${dashed(range)} ppm`,
    index !== null && count > 0 && `step ${index + 1} of ${count}`,
  ]
    .filter(Boolean)
    .join(', ')
  const meaning = text(m.meaning)
  const placeholder = m.placeholder === true
  // The caveats are about the published guideline; placeholder bands are not it.
  const caveats =
    !placeholder && Array.isArray(m.caveats) ? m.caveats.map(text).filter(Boolean) : []

  return (
    <>
      {placeholder && <p className="ff-microbial__warn">{PLACEHOLDER_WARNING}</p>}
      <p className="ff-microbial__reading">
        {READING_LABEL}:{' '}
        <strong>
          {value.toFixed(1)} {RESPIRATION_UNITS}
        </strong>
      </p>
      <div className="ff-microbial__rating">
        <p className="ff-microbial__band">
          Rating: <strong>{text(m.band)}</strong>
          {where && ` (${where})`}
        </p>
        {meaning && <p className="ff-microbial__meaning">{meaning}</p>}
      </div>

      {/* The sentence above carries the rating; the scale is a picture of it. */}
      {bands.length > 1 && (
        <>
          <ol className="ff-microbial__scale" aria-hidden="true">
            {bands.map((b, i) => (
              <li
                key={`${i}-${b.min}`}
                className={`ff-microbial__step${i === index ? ' ff-microbial__step--on' : ''}`}
                title={text(b.label) || undefined}
              >
                {stepText(b, bands[i - 1])}
              </li>
            ))}
          </ol>
          {/* The steps stay bare numbers to fit; their unit goes once, under them. */}
          <p className="ff-microbial__scale-unit" aria-hidden="true">
            Ranges in {RESPIRATION_UNITS}
          </p>
        </>
      )}

      <Citation source={m.source} placeholder={placeholder} />
      {caveats.length > 0 && (
        <ul className="ff-microbial__caveats">
          {caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
    </>
  )
}

/**
 * Soil biology block for the results page.
 *
 * @param {Object} props
 * @param {Object|null} [props.microbial] - `analysis.microbial`: a `rated` or
 *   `unrated` payload from the server, or missing when no soil respiration
 *   value was entered.
 */
export default function MicrobialActivity({ microbial = null }) {
  const m = microbial && typeof microbial === 'object' ? microbial : null
  const value = m ? num(m.value) : null

  let content
  if (!m) {
    content = <p className="ff-microbial__pending">{NOT_PROVIDED_MESSAGE}</p>
  } else if (m.status === 'rated' && value !== null && text(m.band)) {
    content = <Rated m={m} value={value} />
  } else {
    // 'unrated' (the bands could not be loaded) or a payload this page does
    // not recognise: still say what was entered.
    content = (
      <>
        {value !== null && (
          <p className="ff-microbial__reading">
            {READING_LABEL}:{' '}
            <strong>
              {value.toFixed(1)} {RESPIRATION_UNITS}
            </strong>
          </p>
        )}
        <p className="ff-microbial__notice">{text(m.message) || UNRATED_MESSAGE}</p>
      </>
    )
  }

  return (
    <div className="ff-microbial">
      <h3 className="ff-microbial__title">Soil biology</h3>
      {content}
    </div>
  )
}
