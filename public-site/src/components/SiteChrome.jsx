/* ==========================================================================
   Farmers Forward (public site) - header + footer
   Deliberately no admin, login or "team" links anywhere: this site is public
   only.
   ========================================================================== */

import { Link, NavLink } from 'react-router-dom'

import Wordmark from './Wordmark.jsx'

export function SiteHeader({ overlay = false }) {
  return (
    <header className={`site-header${overlay ? ' site-header--overlay' : ''}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="site-header__inner">
        <Link to="/" className="site-header__home" aria-label="Farmers Forward home">
          <Wordmark />
        </Link>
        <nav className="site-header__nav" aria-label="Main">
          <NavLink to="/" end className="site-header__link">Home</NavLink>
          <Link to="/#how" className="site-header__link site-header__link--wide">How it works</Link>
          <NavLink to="/tool" className="btn btn--primary btn--sm">Try it free</NavLink>
        </nav>
      </div>
    </header>
  )
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer__inner">
        <div>
          <Wordmark small />
          <p className="site-footer__line">
            Soil health scores and fertilizer plans for small farms, built
            for the Congressional App Challenge.
          </p>
        </div>
        <div className="site-footer__sources">
          <p className="site-footer__label">Built on</p>
          <ul>
            <li>Texas A&amp;M AgriLife fertilizer recommendation tables</li>
            <li>USDA NRCS soil survey (SSURGO) via UC Davis SoilWeb</li>
            <li>Expert input from Prairie View A&amp;M University</li>
          </ul>
        </div>
      </div>
      <p className="site-footer__fine">
        Results are guidance, not a substitute for a certified soil lab or
        your county AgriLife Extension agent. Satellite imagery © Esri;
        address search © OpenStreetMap contributors.
      </p>
    </footer>
  )
}
