import { useRef, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import api from '../api/client.js'
import Logo from '../components/Logo.jsx'
import './auth.css'

export default function Login() {
  const navigate = useNavigate()
  // Capture the gate once at mount so a successful login (which navigates
  // away) never causes a stale re-guard mid-transition.
  const allowed = useRef(sessionStorage.getItem('ff_secret_ok') === '1').current

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (pending) return
    if (!username.trim() || !password) {
      setError('Invalid credentials')
      return
    }
    setPending(true)
    setError('')
    try {
      await api.login(username.trim(), password)
      sessionStorage.setItem('ff_otp_pending', '1')
      navigate('/otp')
    } catch (err) {
      // Never disclose which field was wrong.
      setError('Invalid credentials')
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
          Enter your credentials to receive a one-time verification code by email.
        </p>

        <form className="ff-auth__form" onSubmit={handleSubmit} noValidate>
          {error && (
            <div className="ff-auth__error" role="alert">
              {error}
            </div>
          )}

          <div className="ff-field">
            <label className="ff-label" htmlFor="ff-login-username">
              Username
            </label>
            <input
              id="ff-login-username"
              className="ff-input"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={username}
              disabled={pending}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          </div>

          <div className="ff-field">
            <label className="ff-label" htmlFor="ff-login-password">
              Password
            </label>
            <input
              id="ff-login-password"
              className="ff-input"
              type="password"
              autoComplete="current-password"
              value={password}
              disabled={pending}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          <div className="ff-auth__actions">
            <button
              type="submit"
              className="ff-btn ff-btn--primary"
              disabled={pending || !username.trim() || !password}
            >
              {pending ? 'Signing in…' : 'Continue'}
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
