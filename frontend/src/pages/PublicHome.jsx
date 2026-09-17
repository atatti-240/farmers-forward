import { useCallback, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client.js'
import Logo from '../components/Logo.jsx'
import Hero from './public/Hero.jsx'
import Mission from './public/Mission.jsx'
import HowItWorks from './public/HowItWorks.jsx'
import WhereWeAre from './public/WhereWeAre.jsx'
import Contact from './public/Contact.jsx'
import './public/public.css'

// After the logo is activated, a short listening window opens during which the
// expected key sequence must be entered. No UI is ever shown.
const SEQ = '202611'
const WINDOW_MS = 30000

export default function PublicHome() {
  const navigate = useNavigate()
  const deadline = useRef(0) // timestamp until which input is accepted (0 = inactive)
  const typed = useRef('')
  const timer = useRef(null)
  const busy = useRef(false)

  const stop = useCallback(() => {
    deadline.current = 0
    typed.current = ''
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  // Logo activation: (re)open the window and discard anything typed so far.
  const arm = useCallback(() => {
    typed.current = ''
    busy.current = false
    deadline.current = Date.now() + WINDOW_MS
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(stop, WINDOW_MS)
  }, [stop])

  useEffect(() => {
    function onKey(e) {
      if (busy.current) return
      if (deadline.current === 0 || Date.now() > deadline.current) return
      if (e.key == null || e.key.length !== 1) return // ignore modifier/control keys

      const next = typed.current + e.key
      if (next === SEQ) {
        // Exact match - verify server-side, then proceed automatically.
        busy.current = true
        stop()
        api
          .verifyCode(next)
          .then((res) => {
            busy.current = false
            if (res && res.ok) {
              sessionStorage.setItem('ff_secret_ok', '1')
              navigate('/login')
            }
          })
          .catch(() => {
            busy.current = false
          })
      } else if (SEQ.startsWith(next)) {
        typed.current = next // still on track
      } else {
        stop() // diverged - reset silently; another activation is required
      }
    }

    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      if (timer.current) clearTimeout(timer.current)
    }
  }, [navigate, stop])

  return (
    <div className="ff-brand ff-public">
      <nav className="ff-nav">
        <div className="ff-nav__inner">
          <Logo variant="public" onSecretUnlock={arm} />
          <div className="ff-nav__links">
            <a href="#mission" className="ff-nav__link">Why we do this</a>
            <a href="#how-it-works" className="ff-nav__link">How it works</a>
            <a href="#where" className="ff-nav__link">Where we work</a>
            <a href="#contact" className="ff-btn ff-btn--sticker ff-btn--sm ff-nav__cta">
              Request a test
            </a>
          </div>
        </div>
      </nav>

      <main>
        <Hero />
        <Mission />
        <HowItWorks />
        <WhereWeAre />
        <Contact />
      </main>

      <footer className="ff-public-footer">
        <div className="ff-container ff-public-footer__inner">
          <span className="ff-public-footer__brand">Farmers Forward</span>
          <span className="ff-public-footer__tag">
            Free soil testing for small farms along the Brazos River.
          </span>
          <span className="ff-public-footer__meta">
            &copy; {new Date().getFullYear()} Farmers Forward &middot; made by hand
            in Fort Bend County, Texas
          </span>
        </div>
      </footer>
    </div>
  )
}
