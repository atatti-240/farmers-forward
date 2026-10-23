/* ==========================================================================
   Farmers Forward (public site) - landing page
   The story (the problem → how it works → try it) and the way into the tool.
   Pings /health on load so the sleeping free server is awake by the time a
   visitor reaches the tool.
   ========================================================================== */

import './landing.css'

import { useEffect } from 'react'
import { Link } from 'react-router-dom'

import { ping } from '../api/client.js'
import { SiteFooter, SiteHeader } from '../components/SiteChrome.jsx'

const STEPS = [
  {
    n: '01',
    title: 'Read the soil',
    body: 'A handheld sensor measures pH, salinity (EC), nitrogen, phosphorus and potassium at points across the field.',
  },
  {
    n: '02',
    title: 'Score every region',
    body: 'Our model turns each reading into a 0–100 soil health score and checks it against the crop you plan to grow.',
  },
  {
    n: '03',
    title: 'Get the plan',
    body: 'Each region gets plain-English fixes and nutrient rates from Texas A&M AgriLife’s soil-test tables, plus a PDF to keep.',
  },
]

export default function Landing() {
  useEffect(() => {
    document.title = 'Farmers Forward · Know your soil before you fertilize'
    ping()
  }, [])

  return (
    <div className="page page--landing">
      <SiteHeader />
      <main id="main">
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero__glow" aria-hidden="true" />
          <div className="hero__inner">
            <p className="eyebrow">Free soil health check for small farms</p>
            <h1 id="hero-title" className="hero__title">
              Know your soil <em>before</em> you fertilize.
            </h1>
            <p className="hero__lead">
              Map your field, enter soil sensor readings, and get a health
              score for every part of it, with a fertilizer plan backed by
              Texas A&amp;M AgriLife. No sign-up.
            </p>
            <div className="hero__ctas">
              <Link to="/tool" className="btn btn--primary btn--lg">Try it on your field</Link>
              <a href="#how" className="btn btn--secondary btn--lg">How it works</a>
            </div>
          </div>
        </section>

        <section className="problem" aria-labelledby="problem-title">
          <div className="wrap">
            <p className="eyebrow">The problem</p>
            <h2 id="problem-title" className="display">
              Most small farms fertilize by guesswork.
            </h2>
            <p className="lead">
              One field can hold acidic corners, salty low spots and
              nitrogen-hungry strips. Treating it all the same wastes money
              where the soil is fine and still comes up short where it isn’t.
              A lab test per region is slow and costly, so many growers skip it.
            </p>
          </div>
        </section>

        <section id="how" className="how" aria-labelledby="how-title">
          <div className="wrap">
            <p className="eyebrow">How Farmers Forward works</p>
            <h2 id="how-title" className="display">Sensor → model → plan.</h2>
            <ol className="how__steps">
              {STEPS.map((s) => (
                <li key={s.n} className="how__step">
                  <span className="how__num mono">{s.n}</span>
                  <h3 className="how__title">{s.title}</h3>
                  <p>{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="cred" aria-labelledby="cred-title">
          <div className="wrap">
            <h2 id="cred-title" className="eyebrow">Built on trusted sources</h2>
            <ul className="cred__list">
              <li><b>Texas A&amp;M AgriLife</b><span>Soil-test fertilizer recommendation tables</span></li>
              <li><b>USDA NRCS SSURGO</b><span>Soil survey data, via UC Davis SoilWeb</span></li>
              <li><b>Prairie View A&amp;M University</b><span>Expert input on the agronomy</span></li>
            </ul>
          </div>
        </section>

        <section className="cta" aria-labelledby="cta-title">
          <div className="wrap cta__inner">
            <h2 id="cta-title" className="display">Try it on your field.</h2>
            <p className="lead">
              Takes about two minutes. No sensor yet? Load our example field
              in Fort Bend County, Texas.
            </p>
            <Link to="/tool" className="btn btn--primary btn--lg">Start the assessment</Link>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  )
}
