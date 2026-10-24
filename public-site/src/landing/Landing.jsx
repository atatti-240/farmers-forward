/* ==========================================================================
   Farmers Forward (public site) - landing page
   Hero → the problem → how it works → sources → try it. Pings /health on
   load so the sleeping free server is awake by the time a visitor reaches
   the tool. Smooth scrolling (Lenis) and scroll-linked motion (GSAP) load
   after first paint, on this page only, and never under reduced motion.
   ========================================================================== */

import './landing.css'

import { useEffect } from 'react'

import { ping } from '../api/client.js'
import Arrow from '../components/Arrow.jsx'
import { SiteFooter, SiteHeader } from '../components/SiteChrome.jsx'
import { WipeLink } from '../components/Wipe.jsx'
import Hero from './Hero.jsx'

const STEPS = [
  {
    n: '01',
    title: 'Read the soil',
    body: 'A handheld sensor measures pH, salinity (EC), nitrogen, phosphorus and potassium at points across the field.',
  },
  {
    n: '02',
    title: 'Score every square',
    body: 'Our model turns each reading into a 0–100 soil health score and checks it against the crop you plan to grow.',
  },
  {
    n: '03',
    title: 'Get the plan',
    body: 'Each square gets plain-English fixes and nutrient rates from Texas A&M AgriLife’s soil-test tables, plus a PDF to keep.',
  },
]

/** Lenis smooth scroll, linked to GSAP's ticker and ScrollTrigger. */
function useSmoothScroll() {
  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return undefined
    let lenis = null
    let cancelled = false
    let tick = null
    Promise.all([import('lenis'), import('gsap'), import('gsap/ScrollTrigger')]).then(
      ([{ default: Lenis }, { gsap }, { ScrollTrigger }]) => {
        if (cancelled) return
        gsap.registerPlugin(ScrollTrigger)
        lenis = new Lenis({ lerp: 0.1, anchors: { offset: -72 } })
        lenis.on('scroll', ScrollTrigger.update)
        tick = (time) => lenis.raf(time * 1000)
        gsap.ticker.add(tick)
        gsap.ticker.lagSmoothing(0)
      })
    return () => {
      cancelled = true
      if (lenis) {
        import('gsap').then(({ gsap }) => gsap.ticker.remove(tick))
        lenis.destroy()
      }
    }
  }, [])
}

export default function Landing() {
  useEffect(() => {
    document.title = 'Farmers Forward · Know your soil before you fertilize'
    ping()
  }, [])
  useSmoothScroll()

  return (
    <div className="page page--landing">
      <SiteHeader overlay />
      <main id="main">
        <Hero />

        <section id="problem" className="lsec problem" aria-labelledby="problem-title">
          <div className="wrap">
            <p className="kicker kicker--rule"><span className="kicker__num">01</span> The problem</p>
            <h2 id="problem-title" className="h2 problem__title">
              Most small farms fertilize <em>by guesswork.</em>
            </h2>
            <p className="lead">
              One field can hold acidic corners, salty low spots and
              nitrogen-hungry strips. Treating it all the same wastes money
              where the soil is fine and still comes up short where it isn’t.
              A lab test per region is slow and costly, so many growers skip it.
            </p>
          </div>
        </section>

        <section id="how" className="lsec how" aria-labelledby="how-title">
          <div className="wrap">
            <p className="kicker kicker--rule"><span className="kicker__num">02</span> How it works</p>
            <h2 id="how-title" className="h2">Sensor, model, <em>plan.</em></h2>
            <ol className="how__steps">
              {STEPS.map((s) => (
                <li key={s.n} className="how__step">
                  <span className="how__num mono">{s.n}</span>
                  <h3 className="h3 how__title">{s.title}</h3>
                  <p className="help">{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="sources" className="lsec cred" aria-labelledby="cred-title">
          <div className="wrap">
            <p className="kicker kicker--rule"><span className="kicker__num">03</span> Sources</p>
            <h2 id="cred-title" className="h2">Built on <em>public science.</em></h2>
            <ul className="cred__list">
              <li><b>Texas A&amp;M AgriLife</b><span>Soil-test fertilizer recommendation tables</span></li>
              <li><b>USDA NRCS SSURGO</b><span>Soil survey data, via UC Davis SoilWeb</span></li>
              <li><b>Prairie View A&amp;M University</b><span>Expert input on the agronomy</span></li>
            </ul>
          </div>
        </section>

        <section className="lsec cta" aria-labelledby="cta-title">
          <div className="wrap">
            <h2 id="cta-title" className="display cta__title">Try it on <em>your field.</em></h2>
            <p className="lead">
              Takes about two minutes. No sensor yet? Load our example field
              in Fort Bend County, Texas.
            </p>
            <WipeLink to="/tool" className="btn btn--primary btn--lg">Start the assessment <Arrow /></WipeLink>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  )
}
