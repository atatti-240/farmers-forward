/* ==========================================================================
   Farmers Forward (public site) - the assessment tool (/tool)
   Stepper + the active step, inside the ToolProvider store. No login.
   ========================================================================== */

import 'leaflet/dist/leaflet.css'
import './tool.css'

import { useEffect } from 'react'

import { ping } from '../api/client.js'
import { SiteFooter, SiteHeader } from '../components/SiteChrome.jsx'
import Step1Field from './Step1Field.jsx'
import Step2Readings from './Step2Readings.jsx'
import Step3Review from './Step3Review.jsx'
import Step4Results from './Step4Results.jsx'
import ToolProvider, { useTool } from './ToolProvider.jsx'

const STEPS = [
  { n: 1, label: 'Map your field' },
  { n: 2, label: 'Soil readings' },
  { n: 3, label: 'Check & run' },
  { n: 4, label: 'Results' },
]

function Stepper() {
  const { step, points, result, goStep } = useTool()
  const reachable = (n) => (n === 1 ? !result : n <= 3 ? points.length > 0 && !result : !!result)
  return (
    <nav className="stepper" aria-label="Assessment steps">
      <ol className="stepper__list">
        {STEPS.map((s) => {
          const active = s.n === step
          const done = s.n < step
          const canGo = reachable(s.n) && !active
          return (
            <li key={s.n} className={`stepper__item${active ? ' is-active' : ''}${done ? ' is-done' : ''}`}>
              <button
                type="button"
                className="stepper__btn"
                aria-current={active ? 'step' : undefined}
                disabled={!canGo}
                onClick={() => goStep(s.n)}
              >
                <span className="stepper__num" aria-hidden="true">{done ? '✓' : s.n}</span>
                <span className="stepper__label">{s.label}</span>
              </button>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

function ToolShell() {
  const { step } = useTool()
  useEffect(() => {
    document.title = 'Assess your field · Farmers Forward'
    // Wake the server while the visitor is still drawing their field.
    ping()
  }, [])

  return (
    <div className="page page--tool">
      <SiteHeader />
      <Stepper />
      <main id="main" className="tool-main">
        {step === 1 && <Step1Field />}
        {step === 2 && <Step2Readings />}
        {step === 3 && <Step3Review />}
        {step === 4 && <Step4Results />}
      </main>
      <SiteFooter />
    </div>
  )
}

export default function ToolPage() {
  return (
    <ToolProvider>
      <ToolShell />
    </ToolProvider>
  )
}
