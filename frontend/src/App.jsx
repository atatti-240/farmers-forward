import { useEffect } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'

import PublicHome from './pages/PublicHome.jsx'
import Login from './pages/Login.jsx'
import Otp from './pages/Otp.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Admins from './pages/Admins.jsx'
import RequireAuth from './components/RequireAuth.jsx'

/** Start each page at the top, as a full page load would (the router keeps
 *  the old scroll position, so /admins opened with its heading under the
 *  sticky header). */
function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pathname])
  return null
}

export default function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route path="/" element={<PublicHome />} />
        <Route path="/login" element={<Login />} />
        <Route path="/otp" element={<Otp />} />
        <Route
          path="/dashboard"
          element={
            <RequireAuth>
              <Dashboard />
            </RequireAuth>
          }
        />
        <Route
          path="/admins"
          element={
            <RequireAuth ownerOnly>
              <Admins />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  )
}
