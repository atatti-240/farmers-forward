/* ==========================================================================
   Farmers Forward (public site) - /credits
   Who made every photo and video on the site, where it came from and under
   which license. Mirrors public-site/CREDITS.md.
   ========================================================================== */

import { useEffect } from 'react'

import { SiteFooter, SiteHeader } from '../components/SiteChrome.jsx'
import { CREDITS } from './credits.js'

export default function Credits() {
  useEffect(() => {
    document.title = 'Photo & video credits · Farmers Forward'
  }, [])

  return (
    <div className="page page--credits">
      <SiteHeader />
      <main id="main" className="credits">
        <p className="kicker kicker--rule">Credits</p>
        <h1 className="h1 credits__title">Photos <em>&amp;</em> video.</h1>
        <p className="lead credits__lead">
          The photos and video on this site are free-license stock from
          Unsplash, Pexels and Pixabay, used under their licenses. We colour
          graded, cropped and compressed them to fit the site. They’re
          illustrative: not our farm and not our team.
        </p>
        <ul className="credits__list">
          {CREDITS.map((c) => (
            <li key={c.what} className="credits__item">
              <p className="credits__what">{c.what}</p>
              <p className="credits__by">
                {c.profile ? <a href={c.profile} target="_blank" rel="noreferrer">{c.creator}</a> : c.creator}
                {' · '}
                <a href={c.source} target="_blank" rel="noreferrer">{c.site}</a>
              </p>
              <p className="credits__license mono">{c.license}</p>
            </li>
          ))}
        </ul>
      </main>
      <SiteFooter />
    </div>
  )
}
