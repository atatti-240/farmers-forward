/* ==========================================================================
   Farmers Forward (public site) - landing hero
   Full-bleed graded field footage (a poster image paints first; the video
   fades in over it once it can play, and only on capable connections),
   the living soil canvas along the bottom, the headline, one call to action,
   and an "example reading" readout whose score counts up while the soil
   underneath comes alive. The readout is a real run of the model on the
   example field's point A7 (made-up readings, labelled as such).
   ========================================================================== */

import { useEffect, useRef, useState } from 'react'

import Arrow from '../components/Arrow.jsx'
import { WipeLink } from '../components/Wipe.jsx'
import SoilCanvas from './soilCanvas.js'
import useMagnetic from './useMagnetic.js'

/** Point A7 of the example field, scored by the model (Corn). */
export const EXAMPLE_READING = {
  point: 'A7',
  crop: 'Corn',
  score: 96,
  category: 'Excellent',
  readings: [
    ['pH', '6.1', ''],
    ['EC', '0.38', 'dS/m'],
    ['N', '27', 'ppm'],
    ['P', '37', 'ppm'],
    ['K', '198', 'ppm'],
  ],
}

const COUNT_DELAY_MS = 900
const COUNT_MS = 2800

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Should the hero play video at all, and which file? */
function videoChoice() {
  if (reducedMotion()) return null
  const conn = navigator.connection
  if (conn && (conn.saveData || /(^|-)2g|3g/.test(conn.effectiveType || ''))) return null
  return window.innerWidth < 768 ? 'small' : 'full'
}

function HeroMedia({ active }) {
  const videoRef = useRef(null)
  const [choice, setChoice] = useState(null)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    // After first paint, so the poster wins the race to the screen.
    const idle = window.requestIdleCallback
    const id = idle ? idle(() => setChoice(videoChoice()), { timeout: 1200 })
      : setTimeout(() => setChoice(videoChoice()), 600)
    return () => (idle ? window.cancelIdleCallback(id) : clearTimeout(id))
  }, [])

  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    if (active) v.play().catch(() => {})
    else v.pause()
  }, [active, choice])

  return (
    <div className="hero__media" aria-hidden="true">
      <picture>
        <source type="image/avif" sizes="100vw"
          srcSet="/media/hero-poster-960.avif 960w, /media/hero-poster-1600.avif 1600w, /media/hero-poster-2400.avif 2400w" />
        <source type="image/webp" sizes="100vw"
          srcSet="/media/hero-poster-960.webp 960w, /media/hero-poster-1600.webp 1600w, /media/hero-poster-2400.webp 2400w" />
        <img className="hero__poster" src="/media/hero-poster-1600.jpg" alt="" fetchPriority="high"
          srcSet="/media/hero-poster-960.jpg 960w, /media/hero-poster-1600.jpg 1600w, /media/hero-poster-2400.jpg 2400w"
          sizes="100vw" />
      </picture>
      {choice && (
        <video
          ref={videoRef}
          className={`hero__video${playing ? ' is-playing' : ''}`}
          muted
          loop
          playsInline
          autoPlay
          preload="auto"
          onPlaying={() => setPlaying(true)}
        >
          {choice === 'full' && <source src="/media/hero.webm" type="video/webm" />}
          <source src={choice === 'full' ? '/media/hero.mp4' : '/media/hero-720.mp4'} type="video/mp4" />
        </video>
      )}
      <div className="hero__grade" />
    </div>
  )
}

export default function Hero() {
  const heroRef = useRef(null)
  const canvasRef = useRef(null)
  const ctaRef = useRef(null)
  const engine = useRef(null)
  const [score, setScore] = useState(0)
  const [visible, setVisible] = useState(true)
  useMagnetic(ctaRef)

  // Soil canvas + pause everything when the hero is off screen. Built after
  // first paint so it never competes with the headline.
  useEffect(() => {
    let cleanup = () => {}
    const idle = window.requestIdleCallback
    const id = idle ? idle(() => { cleanup = startSoil() }, { timeout: 700 })
      : setTimeout(() => { cleanup = startSoil() }, 300)
    return () => {
      if (idle) window.cancelIdleCallback(id)
      else clearTimeout(id)
      cleanup()
    }
  }, [])

  function startSoil() {
    const reduce = reducedMotion()
    const soil = new SoilCanvas(canvasRef.current, { band: 0.27, reduced: reduce })
    engine.current = soil
    const ro = new ResizeObserver(() => soil.resize())
    ro.observe(canvasRef.current)
    const io = new IntersectionObserver(([entry]) => {
      setVisible(entry.isIntersecting)
      if (entry.isIntersecting && !document.hidden) soil.start()
      else soil.stop()
    }, { threshold: 0 })
    io.observe(heroRef.current)
    const onVis = () => (document.hidden ? soil.stop() : soil.start())
    document.addEventListener('visibilitychange', onVis)
    const hero = heroRef.current
    const onMove = (e) => {
      const r = canvasRef.current.getBoundingClientRect()
      soil.setPointer(e.clientX - r.left, e.clientY - r.top)
    }
    hero.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      ro.disconnect()
      io.disconnect()
      document.removeEventListener('visibilitychange', onVis)
      hero.removeEventListener('pointermove', onMove)
      soil.destroy()
    }
  }

  // Count the example score up; the soil grows with it.
  useEffect(() => {
    const target = EXAMPLE_READING.score
    if (reducedMotion()) {
      setScore(target)
      engine.current?.setScore(1)
      return undefined
    }
    let frame = 0
    let start = 0
    const ease = (t) => 1 - Math.pow(1 - t, 3)
    const step = (now) => {
      if (!start) start = now
      const t = Math.min(1, (now - start) / COUNT_MS)
      const v = ease(t)
      setScore(Math.round(v * target))
      engine.current?.setScore(v)
      if (t < 1) frame = requestAnimationFrame(step)
    }
    const timer = setTimeout(() => { frame = requestAnimationFrame(step) }, COUNT_DELAY_MS)
    return () => {
      clearTimeout(timer)
      cancelAnimationFrame(frame)
    }
  }, [])

  const r = EXAMPLE_READING
  return (
    <section ref={heroRef} className="hero" aria-labelledby="hero-title">
      <HeroMedia active={visible} />
      <canvas ref={canvasRef} className="hero__soil" aria-hidden="true" />

      <div className="hero__inner">
        <p className="kicker hero__kicker reveal" style={{ '--d': '0.1s' }}>
          <span className="kicker__num">●</span> Free soil check · Fort Bend County, Texas
        </p>
        <h1 id="hero-title" className="display hero__title">
          <span className="line"><span style={{ '--d': '0.15s' }}>Know your soil</span></span>
          <span className="line"><span style={{ '--d': '0.27s' }}><em>before</em> you fertilize.</span></span>
        </h1>
        <div className="hero__foot">
          <p className="hero__lead">
            Map a field, enter five sensor readings, and get a Texas A&amp;M-backed
            plan for every square of it. Free, no sign-up.
          </p>
          <div className="hero__ctas reveal" style={{ '--d': '0.7s' }}>
            <WipeLink ref={ctaRef} to="/tool" className="btn btn--primary btn--lg hero__cta">
              Try it on your field <Arrow />
            </WipeLink>
            <a href="#how" className="btn btn--ghost btn--lg hero__how">How it works</a>
          </div>
        </div>
      </div>

      <aside className="readout reveal" style={{ '--d': '0.85s' }} aria-label="Example reading">
        <p className="readout__head mono">
          <span>Example reading</span>
          <span className="readout__pt">Point {r.point} · {r.crop}</span>
        </p>
        <dl className="readout__vals">
          {r.readings.map(([k, v, u]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd className="mono">{v}{u && <small> {u}</small>}</dd>
            </div>
          ))}
        </dl>
        <div className="readout__score">
          <span className="readout__label mono">Soil health</span>
          <span className="readout__num mono" aria-live="off">{String(score).padStart(2, '0')}</span>
          <span className="readout__cat">{score === r.score ? r.category : 'Scoring…'}</span>
        </div>
        <div className="readout__bar" aria-hidden="true">
          <span style={{ transform: `scaleX(${score / 100})` }} />
        </div>
        <p className="readout__note">Made-up readings on a real Needville field, scored by our model.</p>
      </aside>

      <a href="#problem" className="hero__scroll mono" aria-label="Scroll to the story">
        <span>Scroll</span>
        <span className="hero__scroll-line" aria-hidden="true" />
      </a>
    </section>
  )
}
