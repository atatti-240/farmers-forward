/* ==========================================================================
   Farmers Forward (public site) - optional result blocks
   Fields that arrive only once the fertilizer-calculator and soil-data
   features are merged into this deployment:
     results[].fertilizer_plan, analysis.fertilizer_total   (fert-calculator)
     analysis.soil_point, analysis.soil_profile              (soil-data)
     analysis.microbial                                      (soil-data)
   Each block renders nothing when its field is absent, so the page looks
   complete either way. The backend writes the plan's wording, so the page
   says exactly what the PDF says.
   ========================================================================== */

import { useEffect, useState } from 'react'

import { soilInfo } from '../api/client.js'

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const text = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null)

/* -------------------------------------------------- fertilizer plan */

/** True when a region's plan has something to show. */
export function hasFertilizerPlan(plan) {
  return !!plan && typeof plan === 'object' && (plan.available || !!text(plan.message))
}

/** The body of a region's fertilizer plan (the card supplies the heading). */
export function FertilizerPlan({ plan }) {
  if (!hasFertilizerPlan(plan)) return null
  if (!plan.available) return <p className="help">{plan.message}</p>
  const items = Array.isArray(plan.items) ? plan.items : []
  return (
    <>
      {plan.area_text && <p className="help">{plan.area_text}</p>}
      <ul className="plan">
        {items.map((item, i) => (item.kind === 'product' ? (
          <li key={`${item.label}-${i}`} className="plan__product">
            <span className="plan__name">{item.label}</span>
            <span className="plan__amount mono">{item.amounts}</span>
            {item.detail && <span className="plan__detail">{item.detail}</span>}
          </li>
        ) : (
          <li key={`${item.kind}-${item.nutrient}-${i}`} className={`plan__line plan__line--${item.kind}`}>
            {item.text}
          </li>
        )))}
      </ul>
      {(plan.warnings || []).map((t) => <p key={t} className="note note--warn"><b>Heads up:</b> {t}</p>)}
      {(plan.notes || []).map((t) => <p key={t} className="help">{t}</p>)}
      {plan.source && <p className="source">{plan.source}</p>}
    </>
  )
}

export function FieldFertilizerTotal({ total }) {
  if (!total || typeof total !== 'object') return null
  const products = Array.isArray(total.products) ? total.products : []
  const excluded = Array.isArray(total.excluded) ? total.excluded : []
  if (!products.length && !excluded.length && !total.n_regions) return null
  return (
    <section className="fpanel" aria-labelledby="fert-total-title">
      <p className="kicker">Whole field</p>
      <h3 id="fert-total-title" className="h3 fpanel__title">Fertilizer total</h3>
      {total.summary && <p className="help">{total.summary}</p>}
      {products.length > 0 && (
        <ul className="plan">
          {products.map((p) => (
            <li key={p.label} className="plan__product">
              <span className="plan__name">{p.label}</span>
              <span className="plan__amount mono">{p.amounts}</span>
            </li>
          ))}
        </ul>
      )}
      {total.none_needed && <p className="help">No fertilizer needed across the field.</p>}
      {excluded.length > 0 && (
        <>
          <p className="label fpanel__sub">Not included</p>
          <ul className="bullets">{excluded.map((t) => <li key={t}>{t}</li>)}</ul>
        </>
      )}
    </section>
  )
}

/* ------------------------------------------------------ soil profile */

function soilFacts(p) {
  const facts = []
  if (text(p.series)) facts.push(['Soil series', text(p.series)])
  if (text(p.map_unit)) facts.push(['Map unit', text(p.map_unit)])
  const fractions = [[num(p.sand_pct), 'sand'], [num(p.silt_pct), 'silt'], [num(p.clay_pct), 'clay']]
    .filter(([v]) => v !== null).map(([v, n]) => `${Math.round(v)}% ${n}`).join(', ')
  const texture = [text(p.texture), fractions].filter(Boolean).join(': ')
  if (texture) facts.push(['Surface texture', texture])
  if (text(p.drainage)) facts.push(['Drainage', text(p.drainage)])
  if (text(p.hydrologic_group)) facts.push(['Hydrologic group', text(p.hydrologic_group)])
  if (num(p.cec) !== null) facts.push(['CEC (nutrient holding)', `${Math.round(p.cec * 10) / 10} meq/100 g`])
  return facts
}

/** A mapped soil profile worth showing, or null. */
export function usableProfile(profile) {
  if (!profile || !(profile.status === 'ok' || profile.status === 'partial')) return null
  return num(profile.organic_matter_pct) !== null || soilFacts(profile).length > 0 ? profile : null
}

/**
 * Mapped USDA soil survey data for the field (typical values for the soil
 * type, never a measurement and never part of the score). Uses the profile
 * that came with the analysis, or looks it up once if only the point came
 * back. Renders nothing when the feature isn't live (404) or has no data.
 */
export function SoilProfile({ point, profile: given }) {
  const [profile, setProfile] = useState(given || null)
  const settled = given && (given.status === 'ok' || given.status === 'no_data')

  useEffect(() => {
    if (settled || !point) return undefined
    const controller = new AbortController()
    soilInfo(point.lat, point.lon, { signal: controller.signal })
      .then((p) => setProfile(p))
      .catch(() => { /* feature not live, or lookup failed: keep what we have */ })
    return () => controller.abort()
  }, [settled, point])

  const usable = usableProfile(profile)
  if (!usable) return null
  const om = num(usable.organic_matter_pct)
  const facts = soilFacts(usable)
  return (
    <section className="fpanel" aria-labelledby="soil-title">
      <p className="kicker">Whole field · USDA soil survey</p>
      <h3 id="soil-title" className="h3 fpanel__title">Soil profile</h3>
      {om !== null && (
        <p className="fpanel__stat">
          <span className="fpanel__big mono">{om.toFixed(1)}%</span>
          <span className="help">typical organic matter for this soil type</span>
        </p>
      )}
      {facts.length > 0 && (
        <dl className="facts">
          {facts.map(([k, v]) => (
            <div key={k} className="facts__row"><dt>{k}</dt><dd>{v}</dd></div>
          ))}
        </dl>
      )}
      <p className="source">
        {text(usable.source_note) || 'Mapped soil survey data (USDA NRCS SSURGO), not a measurement of this field.'}
      </p>
    </section>
  )
}

/* ----------------------------------------------------- soil biology */

/** `analysis.microbial` worth showing (rated or unrated), or null. */
export function usableMicrobial(m) {
  return m && typeof m === 'object' && (m.status === 'rated' || m.status === 'unrated') ? m : null
}

/** One line for a region card: the field's microbial rating. */
export function microbialLine(m) {
  const u = usableMicrobial(m)
  if (!u) return null
  const value = num(u.value)
  if (u.status === 'rated' && text(u.band)) {
    return `${text(u.band)}${value !== null ? ` · ${value.toFixed(1)} ppm CO₂-C` : ''} (24-hour CO₂ burst)`
  }
  return text(u.message) || 'Microbial activity rating is unavailable right now.'
}

/** Soil respiration (24-hour CO₂ burst) for the whole field, display only. */
export function SoilBiology({ microbial }) {
  const m = usableMicrobial(microbial)
  if (!m) return null
  const value = num(m.value)
  const src = m.source && typeof m.source === 'object' ? m.source : {}
  return (
    <section className="fpanel" aria-labelledby="bio-title">
      <p className="kicker">Whole field · lab value</p>
      <h3 id="bio-title" className="h3 fpanel__title">Microbial activity</h3>
      {value !== null && (
        <p className="fpanel__stat">
          <span className="fpanel__big mono">{value.toFixed(1)}</span>
          <span className="help">ppm CO₂-C, 24-hour CO₂ burst</span>
        </p>
      )}
      {m.status === 'rated' && text(m.band)
        ? <p>Rating: <b>{text(m.band)}</b>{text(m.range_text) ? ` (${text(m.range_text)} ppm)` : ''}. {text(m.meaning)}</p>
        : <p className="help">{text(m.message) || 'Microbial activity rating is unavailable right now.'}</p>}
      {text(src.title) && (
        <p className="source">
          Rating bands: {text(src.title)}{text(src.publisher) ? `, ${text(src.publisher)}` : ''}. A published lab
          guideline, not calibrated for Texas soils. Never part of the health score.
        </p>
      )}
    </section>
  )
}
