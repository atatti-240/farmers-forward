/* ==========================================================================
   Farmers Forward (public site) - routes
   /      landing page
   /tool  the assessment (lazy-loaded: Leaflet stays out of the landing bundle)
   /credits  photo and video credits
   Anything else falls back to the landing page. There are no admin, login or
   saved-results routes on this site.
   ========================================================================== */

import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import WakeNotice from './components/WakeNotice.jsx'
import { WipeProvider } from './components/Wipe.jsx'
import Landing from './landing/Landing.jsx'

const ToolPage = lazy(() => import('./tool/ToolPage.jsx'))
const Credits = lazy(() => import('./credits/Credits.jsx'))

/** Scroll to `#hash` targets after navigation, else to the top. */
function ScrollManager() {
  const { pathname, hash } = useLocation()
  useEffect(() => {
    if (hash) {
      const el = document.getElementById(hash.slice(1))
      if (el) {
        el.scrollIntoView()
        return
      }
    }
    window.scrollTo(0, 0)
  }, [pathname, hash])
  return null
}

function Loading() {
  return (
    <div className="route-loading" role="status">
      <span className="kicker">Loading</span>
      <span className="route-loading__bar" aria-hidden="true" />
    </div>
  )
}

export default function App() {
  return (
    <WipeProvider>
      <ScrollManager />
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/tool" element={<ToolPage />} />
          <Route path="/credits" element={<Credits />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
      <WakeNotice />
    </WipeProvider>
  )
}
