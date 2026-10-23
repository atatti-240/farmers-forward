/* ==========================================================================
   Farmers Forward (public site) - optional result blocks
   Fields that arrive only once the fertilizer-calculator and soil-data
   features are merged into this deployment:
     results[].fertilizer_plan, analysis.fertilizer_total   (fert-calculator)
     analysis.soil_point, analysis.soil_profile              (soil-data)
   Each block renders nothing when its field is absent, so the page looks
   complete either way. The backend writes the plan's wording, so the page
   says exactly what the PDF says.
   ========================================================================== */

import { useEffect, useState } from 'react'

import { soilInfo } from '../api/client.js'

/* -------------------------------------------------- fertilizer plan */

export function FertilizerPlan({ plan }) {
  if (!plan || typeof plan !== 'object') return null
  if (!plan.available) {
    return plan.message ? (
      <div className="block">
        <h4 className="block__title">Fertilizer plan</h4>
        <p className="help">{plan.message}</p>
      </div>
    ) : null
  }
  const items = Array.isArray(plan.items) ? plan.items : []
  return (
    <div className="block">
      <h4 className="block__title">Fertilizer plan</h4>
      {plan.area_text && <p className="help">{plan.area_text}</p>}
      <ul className="plan-list">
        {items.map((item, i) => (item.kind === 'product' ? (
          <li key={`${item.label}-${i}`} className="plan-list__product">
            <b>{item.label}</b>
            <span>{item.amounts}</span>
            {item.detail && <span className="help">{item.detail}</span>}
          </li>
        ) : (
          <li key={`${item.kind}-${item.nutrient}-${i}`} className={`plan-list__line plan-list__line--${item.kind}`}>
            {item.text}
          </li>
        )))}
      </ul>
      {(plan.warnings || []).map((t) => <p key={t} className="note note--warn"><b>Heads up:</b> {t}</p>)}
      {(plan.notes || []).map((t) => <p key={t} className="help">{t}</p>)}
      {plan.source && <p className="source">{plan.source}</p>}
    </div>
  )
}

export function FieldFertilizerTotal({ total }) {
  if (!total || typeof total !== 'object') return null
  const products = Array.isArray(total.products) ? total.products : []
  const excluded = Array.isArray(total.excluded) ? total.excluded : []
  if (!products.length && !excluded.length && !total.n_regions) return null
  return (
    <section className="panel panel--wide" aria-labelledby="fert-total-title">
      <h2 id="fert-total-title" className="section-title">Whole-field fertilizer total</h2>
      {total.summary && <p className="help">{total.summary}</p>}
      {products.length > 0 && (
        <ul className="plan-list">
          {products.map((p) => (
            <li key={p.label} className="plan-list__product"><b>{p.label}</b><span>{p.amounts}</span></li>
          ))}
        </ul>
      )}
      {total.none_needed && <p className="help">No fertilizer needed across the field.</p>}
      {excluded.length > 0 && (
        <>
          <h3 className="block__title">Not included</h3>
          <ul className="bullets">{excluded.map((t) => <li key={t}>{t}</li>)}</ul>
        </>
      )}
    </section>
  )
}

/* ------------------------------------------------------ soil profile */

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const text = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null)

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

  if (!profile || !(profile.status === 'ok' || profile.status === 'partial')) return null
  const om = num(profile.organic_matter_pct)
  const facts = soilFacts(profile)
  if (om === null && facts.length === 0) return null
  return (
    <section className="panel panel--wide" aria-labelledby="soil-title">
      <h2 id="soil-title" className="section-title">Soil profile</h2>
      {om !== null && (
        <p className="soil-om">
          Typical organic matter for this soil type: <strong className="mono">{om.toFixed(1)}%</strong>
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
        {text(profile.source_note) || 'Mapped soil survey data (USDA NRCS SSURGO), not a measurement of this field.'}
      </p>
    </section>
  )
}
