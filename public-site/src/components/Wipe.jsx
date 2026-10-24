/* ==========================================================================
   Farmers Forward (public site) - page wipe
   Moving between the landing page and the tool, a navy panel rises over the
   page, the route changes underneath it, and it lifts off the top. Skipped
   under prefers-reduced-motion. <WipeLink> is a drop-in for <Link>.
   ========================================================================== */

import { createContext, useCallback, useContext, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

const IN_MS = 550
const OUT_MS = 700

const WipeContext = createContext(null)

const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function WipeProvider({ children }) {
  const navigate = useNavigate()
  const [phase, setPhase] = useState('idle')
  const busy = useRef(false)

  const go = useCallback((to) => {
    if (busy.current) return
    if (reducedMotion()) {
      navigate(to)
      return
    }
    busy.current = true
    setPhase('in')
    setTimeout(() => {
      navigate(to)
      setPhase('out')
      setTimeout(() => {
        setPhase('idle')
        busy.current = false
      }, OUT_MS)
    }, IN_MS)
  }, [navigate])

  return (
    <WipeContext.Provider value={go}>
      {children}
      <div className={`wipe${phase === 'idle' ? '' : ` is-${phase}`}`} aria-hidden="true" />
    </WipeContext.Provider>
  )
}

/** A <Link> that plays the wipe when it changes page. */
export function WipeLink({ to, onClick, children, ...rest }) {
  const go = useContext(WipeContext)
  const { pathname } = useLocation()
  const target = typeof to === 'string' ? to.split('#')[0] || '/' : to.pathname
  return (
    <Link
      to={to}
      onClick={(event) => {
        onClick?.(event)
        if (event.defaultPrevented || !go || target === pathname) return
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
        event.preventDefault()
        go(to)
      }}
      {...rest}
    >
      {children}
    </Link>
  )
}
