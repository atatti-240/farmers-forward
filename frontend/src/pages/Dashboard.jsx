/* ==========================================================================
   Farmers Forward - Dashboard shell (BUILD_CONTRACT §5)
   The shared sticky admin header (AdminNav) + a 4-step stepper, wrapping the
   whole flow in the DashboardProvider store and rendering the active step.
   ========================================================================== */

import AdminNav from '../components/AdminNav.jsx'
import DashboardProvider, {
  useDashboard,
} from '../dashboard/DashboardProvider.jsx'
import Step1FieldSetup from '../dashboard/Step1FieldSetup.jsx'
import Step2DataEntry from '../dashboard/Step2DataEntry.jsx'
import Step3Review from '../dashboard/Step3Review.jsx'
import Step4Results from '../dashboard/Step4Results.jsx'
import '../dashboard/dashboard.css'
import '../dashboard/admin.css'

const STEPS = [
  { n: 1, label: 'Field setup' },
  { n: 2, label: 'Data entry' },
  { n: 3, label: 'Review' },
  { n: 4, label: 'Results' },
]

function DashboardShell() {
  const { step, points, analysis, goStep } = useDashboard()

  function canGoTo(n) {
    if (n === 1) return true
    if (n === 2 || n === 3) return points.length >= 1
    if (n === 4) return !!analysis
    return false
  }

  let current = null
  if (step === 1) current = <Step1FieldSetup />
  else if (step === 2) current = <Step2DataEntry />
  else if (step === 3) current = <Step3Review />
  else if (step === 4) current = <Step4Results />

  return (
    <div className="ff-brand ff-admin ff-dash">
      <AdminNav />

      <div className="ff-dash__stepbar">
        <div className="ff-dash__stepbar-inner">
          <ol className="ff-stepper" aria-label="Field analysis steps">
            {STEPS.map((s) => {
              const active = s.n === step
              const done = s.n < step
              const reachable = canGoTo(s.n)
              const clickable = reachable && !active
              return (
                <li
                  key={s.n}
                  className={`ff-step${active ? ' ff-step--active' : ''}${done ? ' ff-step--done' : ''}${!reachable ? ' ff-step--locked' : ''}`}
                  data-clickable={clickable}
                  aria-current={active ? 'step' : undefined}
                  aria-disabled={!reachable}
                  role="button"
                  tabIndex={clickable ? 0 : -1}
                  onClick={() => {
                    if (clickable) goStep(s.n)
                  }}
                  onKeyDown={(e) => {
                    if (clickable && (e.key === 'Enter' || e.key === ' ')) {
                      e.preventDefault()
                      goStep(s.n)
                    }
                  }}
                >
                  <span className="ff-step__num">{done ? '✓' : s.n}</span>
                  {s.label}
                </li>
              )
            })}
          </ol>
        </div>
      </div>

      <main className="ff-dash__main">
        <p className="ff-eyebrow">A little care for every acre.</p>
        {current}
      </main>
      <footer className="ff-dash__footer">Farmers Forward · Rooted in healthier soil.</footer>
    </div>
  )
}

/**
 * Private dashboard page. Guarded by `<RequireAuth>` in App.jsx.
 */
export default function Dashboard() {
  return (
    <DashboardProvider>
      <DashboardShell />
    </DashboardProvider>
  )
}
