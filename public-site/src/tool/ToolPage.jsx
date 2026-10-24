/* ==========================================================================
   Farmers Forward (public site) - the assessment tool (/tool)
   Progress rail + the active step, inside the ToolProvider store. No login.
   The rail shows four stages (Find → Field → Readings → Results) over the
   store's four steps: step 1 is "Find" until the visitor starts tracing a
   field and "Field" after, and the check-and-run step sits under Readings.
   ========================================================================== */

import 'leaflet/dist/leaflet.css'
import './tool.css'

import { useEffect, useState } from 'react'

import { ping } from '../api/client.js'
import { SiteFooter, SiteHeader } from '../components/SiteChrome.jsx'
import Step1Field from './Step1Field.jsx'
import Step2Readings from './Step2Readings.jsx'
import Step3Review from './Step3Review.jsx'
import Step4Results from './Step4Results.jsx'
import ToolProvider, { useTool } from './ToolProvider.jsx'

const STAGES = [
  { key: 'find', label: 'Find' },
  { key: 'field', label: 'Field' },
  { key: 'readings', label: 'Readings' },
  { key: 'results', label: 'Results' },
]

function currentStage(step, fieldStarted) {
  if (step === 1) return fieldStarted ? 1 : 0
  if (step === 4) return 3
  return 2
}

function ProgressRail({ fieldStarted }) {
  const { step, points, polygon, result, completeCount, total, goStep } = useTool()
  const stage = currentStage(step, fieldStarted)

  // How far along the whole job is, for the blue line under the stages.
  let fill
  if (step === 4) fill = 1
  else if (step === 3) fill = 0.88
  else if (step === 2) fill = 0.5 + 0.34 * (total ? completeCount / total : 0)
  else if (points.length) fill = 0.42
  else fill = fieldStarted ? 0.28 : 0.08

  const reachable = (i) => {
    if (result) return i === 3
    if (i === 0) return true
    if (i === 1) return polygon.length >= 3
    if (i === 2) return points.length > 0
    return false
  }
  const target = (i) => (i <= 1 ? 1 : i === 2 ? 2 : 4)

  return (
    <nav className="rail" aria-label="Assessment progress">
      <div className="rail__inner">
        <ol className="rail__list">
          {STAGES.map((s, i) => {
            const active = i === stage
            const done = i < stage
            const canGo = !active && reachable(i) && target(i) !== step
            return (
              <li key={s.key} className={`rail__item${active ? ' is-active' : ''}${done ? ' is-done' : ''}`}>
                <button
                  type="button"
                  className="rail__btn"
                  aria-label={`Stage ${i + 1} of 4: ${s.label}${done ? ', done' : ''}`}
                  aria-current={active ? 'step' : undefined}
                  disabled={!canGo}
                  onClick={() => goStep(target(i))}
                >
                  <span className="rail__num mono" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
                  <span className="rail__label">{s.label}</span>
                  {step === 3 && i === 2 && <span className="rail__sub" aria-hidden="true">· check</span>}
                </button>
              </li>
            )
          })}
        </ol>
        <div className="rail__track" aria-hidden="true">
          <span className="rail__fill" style={{ transform: `scaleX(${fill})` }} />
        </div>
      </div>
    </nav>
  )
}

function ToolShell() {
  const { step } = useTool()
  const [fieldStarted, setFieldStarted] = useState(false)

  useEffect(() => {
    document.title = 'Assess your field · Farmers Forward'
    // Wake the server while the visitor is still drawing their field.
    ping()
  }, [])

  return (
    <div className="page page--tool">
      <SiteHeader />
      <ProgressRail fieldStarted={fieldStarted} />
      <main id="main" className="tool">
        {step === 1 && <Step1Field onFieldStarted={setFieldStarted} />}
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
