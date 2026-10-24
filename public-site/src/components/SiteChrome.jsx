/* ==========================================================================
   Farmers Forward (public site) - header + footer
   Deliberately no admin, login or "team" links anywhere: this site is public
   only.
   ========================================================================== */

import { useEffect, useState } from 'react'

import Arrow from './Arrow.jsx'
import { WipeLink } from './Wipe.jsx'
import Wordmark from './Wordmark.jsx'

/**
 * @param {{ overlay?: boolean }} props - `overlay` floats the header over the
 *   landing hero, transparent until the page scrolls.
 */
export function SiteHeader({ overlay = false }) {
  const [docked, setDocked] = useState(false)

  useEffect(() => {
    if (!overlay) return undefined
    const onScroll = () => setDocked(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [overlay])

  return (
    <header className={`hdr${overlay ? ' hdr--overlay' : ''}${docked ? ' is-docked' : ''}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="hdr__inner">
        <WipeLink to="/" className="hdr__home" aria-label="Farmers Forward home">
          <Wordmark />
        </WipeLink>
        <nav className="hdr__nav" aria-label="Main">
          <WipeLink to="/#how" className="hdr__link hdr__link--wide">How it works</WipeLink>
          <WipeLink to="/#sources" className="hdr__link hdr__link--wide">Sources</WipeLink>
          <WipeLink to="/tool" className="btn btn--primary btn--sm hdr__cta">
            Try it free <Arrow />
          </WipeLink>
        </nav>
      </div>
    </header>
  )
}

export function SiteFooter() {
  return (
    <footer className="ftr">
      <div className="ftr__inner">
        <div className="ftr__grid">
          <div className="ftr__brand">
            <Wordmark small />
            <p className="ftr__line">
              Free soil health scores and fertilizer plans for small farms.
              Built in Fort Bend County, Texas for the Congressional App
              Challenge.
            </p>
          </div>
          <div>
            <p className="kicker ftr__label">Built on</p>
            <ul className="ftr__list">
              <li>Texas A&amp;M AgriLife soil-test fertilizer tables</li>
              <li>USDA NRCS soil survey (SSURGO) via UC Davis SoilWeb</li>
              <li>Expert input from Prairie View A&amp;M University</li>
            </ul>
          </div>
          <div>
            <p className="kicker ftr__label">Site</p>
            <ul className="ftr__list">
              <li><WipeLink to="/tool">Assess a field</WipeLink></li>
              <li><WipeLink to="/#how">How it works</WipeLink></li>
              <li><WipeLink to="/credits">Photo &amp; video credits</WipeLink></li>
            </ul>
          </div>
        </div>
        <div className="ftr__base">
          <p className="ftr__fine">
            Results are guidance, not a substitute for a certified soil lab or
            your county AgriLife Extension agent. Satellite imagery © Esri;
            address search © OpenStreetMap contributors. Photos and video are
            free-license stock, not our farm or our team.
          </p>
          <p className="mono">© {new Date().getFullYear()} Farmers Forward</p>
        </div>
      </div>
    </footer>
  )
}
