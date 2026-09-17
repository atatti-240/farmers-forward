import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'

import api from '../api/client.js'
import Spinner from './Spinner.jsx'

/**
 * Route guard for the private area (BUILD_CONTRACT §3.2).
 * On mount it calls `api.me()`. While the check is pending it renders a
 * centered spinner; once resolved it renders `children` when authed,
 * otherwise redirects to `/login`.
 *
 * @param {Object} props
 * @param {React.ReactNode} props.children
 */
export default function RequireAuth({ children }) {
  // null = pending, true = authed, false = not authed
  const [authed, setAuthed] = useState(null)

  useEffect(() => {
    let active = true
    api
      .me()
      .then((res) => {
        if (active) setAuthed(Boolean(res && res.authed))
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

  return children
}
