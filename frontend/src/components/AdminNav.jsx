import { Link, NavLink, useNavigate } from 'react-router-dom'

import api from '../api/client.js'
import { useAdmin } from './AdminContext.jsx'
import Logo from './Logo.jsx'

// Every sessionStorage gate the login flow sets (UX only; the server enforces
// the session itself).
const LOGIN_KEYS = ['ff_secret_ok', 'ff_otp_pending', 'ff_login_email', 'ff_code_requested_at']

/**
 * Sticky header for the private area, shared by the dashboard and the Admins
 * page: the logo (→ home), who is logged in, the owner-only page links and
 * Log out. Logging out ends the server session, clears the login flow's
 * sessionStorage keys and goes back to the public page.
 */
export default function AdminNav() {
  const navigate = useNavigate()
  const admin = useAdmin()
  const isOwner = Boolean(admin && admin.role === 'owner')

  async function handleLogout() {
    try {
      await api.logout()
    } catch {
      /* clear local session regardless of network result */
    }
    for (const key of LOGIN_KEYS) sessionStorage.removeItem(key)
    navigate('/')
  }

  return (
    <nav className="ff-nav ff-dash__nav" aria-label="Admin navigation">
      <div className="ff-nav__inner">
        <div className="ff-dash__brand">
          <Link to="/" aria-label="Farmers Forward home"><Logo variant="dashboard" /></Link>
          <span className="ff-dash__brand-note">The field notebook</span>
        </div>
        <div className={`ff-dash__account${isOwner ? ' ff-dash__account--owner' : ''}`}>
          {admin && (
            <span className="ff-dash__who" title={admin.display_name}>
              <span className="ff-dash__who-label">Logged in as </span>
              <strong>{admin.display_name}</strong>
            </span>
          )}
          {isOwner && (
            <ul className="ff-dash__links">
              <li>
                <NavLink to="/dashboard" end className="ff-dash__link">
                  Field notebook
                </NavLink>
              </li>
              <li>
                <NavLink to="/admins" end className="ff-dash__link">
                  Admins
                </NavLink>
              </li>
            </ul>
          )}
          <button
            type="button"
            className="ff-btn ff-btn--ghost ff-dash__logout"
            onClick={handleLogout}
          >
            Log out
          </button>
        </div>
      </div>
    </nav>
  )
}
