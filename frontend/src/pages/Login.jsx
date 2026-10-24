import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import api from '../api/client.js'
import Logo from '../components/Logo.jsx'
import './auth.css'

export default function Login() {
  const navigate = useNavigate()
  // Capture the gate once at mount so a successful login (which navigates
  // away) never causes a stale re-guard mid-transition.
  const allowed = useRef(sessionStorage.getItem('ff_secret_ok') === '1').current

  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const inputRef = useRef(null)

  // After an error the field is enabled again; put the cursor back in it
  // (focusing it while it is still disabled would be a no-op).
  useEffect(() => {
    if (error && !pending) inputRef.current?.focus()
  }, [error, pending])

  async function handleSubmit(e) {
    e.preventDefault()
    const address = email.trim()
    if (pending || !address) return
    setPending(true)
    setError('')
    try {
      // Same reply whether or not the email is registered; /otp words its
      // copy so it never claims a code was definitely sent.
      await api.login(address)
      sessionStorage.setItem('ff_otp_pending', '1')
      sessionStorage.setItem('ff_login_email', address)
      sessionStorage.setItem('ff_code_requested_at', String(Date.now()))
      navigate('/otp')
    } catch (err) {
      const status = err && err.status
      const payload = (err && err.payload) || {}
      if (status === 403) {
        // The secret gate expired server-side: start over from the home page.
        sessionStorage.removeItem('ff_secret_ok')
        navigate('/')
        return
      }
      setError(
        status === 400 && payload.error
          ? payload.error
          : 'Something went wrong. Please try again.'
      )
      setPending(false)
    }
  }

  if (!allowed) return <Navigate to="/" replace />

  return (
    <div className="ff-brand ff-admin ff-auth">
      <div className="ff-auth__card">
        <div className="ff-auth__logo">
          <Link to="/" aria-label="Farmers Forward home"><Logo variant="dashboard" /></Link>
        </div>

        <p className="ff-eyebrow ff-auth__eyebrow">Welcome back.</p>
        <h1 className="ff-auth__title">Admin sign in</h1>
        <p className="ff-auth__subtitle">
          Enter your admin email and we&apos;ll send you a one-time code.
        </p>

        <form className="ff-auth__form" onSubmit={handleSubmit} noValidate>
          {error && (
            <div className="ff-auth__error" role="alert">
              {error}
            </div>
          )}

          <div className="ff-field">
            <label className="ff-label" htmlFor="ff-login-email">
              Email
            </label>
            <input
              id="ff-login-email"
              ref={inputRef}
              className="ff-input"
              type="email"
              autoComplete="email"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={email}
              disabled={pending}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
            />
          </div>

          <div className="ff-auth__actions">
            <button
              type="submit"
              className="ff-btn ff-btn--primary"
              disabled={pending || !email.trim()}
            >
              {pending ? 'Sending…' : 'Send code'}
            </button>
          </div>
        </form>

        <p className="ff-auth__footer">
          Protected area. Authorized personnel only.
        </p>
      </div>
    </div>
  )
}
