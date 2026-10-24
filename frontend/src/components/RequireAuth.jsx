import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'

import api from '../api/client.js'
import { AdminContext } from './AdminContext.jsx'
import Spinner from './Spinner.jsx'

/**
 * Route guard for the private area (BUILD_CONTRACT §3.2).
 * On mount it calls `api.me()`. While the check is pending it renders a
 * centered spinner; once resolved it renders `children` when authed,
 * otherwise redirects to `/login`. Authed children get the signed-in admin's
 * profile through `AdminContext` (read it with `useAdmin()`).
 *
 * @param {Object} props
 * @param {React.ReactNode} props.children
 * @param {boolean} [props.ownerOnly=false] - send a signed-in admin who is
 *   not an owner to `/dashboard` instead.
 */
export default function RequireAuth({ children, ownerOnly = false }) {
  // null = pending, true = authed, false = not authed
  const [authed, setAuthed] = useState(null)
  // { id, email, display_name, role } once authed
  const [admin, setAdmin] = useState(null)

  useEffect(() => {
    let active = true
    api
      .me()
      .then((res) => {
        if (!active) return
        setAdmin((res && res.authed && res.admin) || null)
        setAuthed(Boolean(res && res.authed))
      })
      .catch(() => {
        if (active) setAuthed(false)
      })
    return () => {
      active = false
    }
  }, [])

  if (authed === null) {
    return <div className="ff-brand ff-admin"><Spinner center /></div>
  }

  if (!authed) {
    return <Navigate to="/login" replace />
  }

  if (ownerOnly && !(admin && admin.role === 'owner')) {
    return <Navigate to="/dashboard" replace />
  }

  return <AdminContext.Provider value={admin}>{children}</AdminContext.Provider>
}
