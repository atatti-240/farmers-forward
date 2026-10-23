/* ==========================================================================
   Farmers Forward - Fertilizer plan (results page)
   Output of the passive Texas A&M fertilizer calculator (backend/fert_calc.py):
   a section in each sample's result card and a whole-field total under the
   cards. The backend writes every line, so the page says exactly what the
   PDF says. Results saved before the calculator existed render nothing.
   ========================================================================== */

import './fertilizer.css'

/** The "Fertilizer plan" section of one sample's result card. */
export default function FertilizerPlan({ plan }) {
  if (!plan) return null
  if (!plan.available) {
    return (
      <div className="ff-fert">
        <p className="ff-result-sub">Fertilizer plan</p>
        <p className="ff-help ff-mb-0">{plan.message}</p>
      </div>
    )
  }
  return (
    <div className="ff-fert">
      <p className="ff-result-sub">Fertilizer plan</p>
      <p className="ff-fert__area">{plan.area_text}</p>
      <ul className="ff-fert__list">
        {(plan.items || []).map((item, i) =>
          item.kind === 'product' ? (
            <li key={`${item.label}-${i}`} className="ff-fert__product">
              <b>{item.label}:</b> {item.amounts}
              <span className="ff-fert__detail">{item.detail}</span>
            </li>
          ) : (
            <li
              key={`${item.kind}-${item.nutrient}-${i}`}
              className={`ff-fert__line ff-fert__line--${item.kind}`}
            >
              {item.text}
            </li>
          )
        )}
      </ul>
      {(plan.warnings || []).map((text) => (
        <p key={text} className="ff-fert__warn">
          <b>Warning:</b> {text}
        </p>
      ))}
      {(plan.notes || []).map((text) => (
        <p key={text} className="ff-fert__note">
          {text}
        </p>
      ))}
      {plan.source && <p className="ff-fert__source">{plan.source}</p>}
    </div>
  )
}

/** Fertilizer totals per product across every sample, under the cards. */
export function FieldFertilizerTotal({ total }) {
  if (!total) return null
  const products = total.products || []
  const excluded = total.excluded || []
  if (!products.length && !excluded.length && !total.n_samples) return null
  return (
    <div className="ff-card ff-fert-total">
      <h2 className="ff-fert-total__title">Whole Field Total</h2>
      <p className="ff-help ff-mb-0">{total.summary}</p>
      {products.length > 0 && (
        <ul className="ff-fert__list ff-fert-total__list">
          {products.map((p) => (
            <li key={p.label} className="ff-fert__product">
              <b>{p.label}:</b> {p.amounts}
            </li>
          ))}
        </ul>
      )}
      {total.none_needed && (
        <p className="ff-fert__line ff-fert__line--none_needed">
          No fertilizer needed across the field.
        </p>
      )}
      {excluded.length > 0 && (
        <div className="ff-fert-total__excluded">
          <p className="ff-result-sub">Not included</p>
          <ul>
            {excluded.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
