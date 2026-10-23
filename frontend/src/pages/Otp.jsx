import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import api from '../api/client.js'
import Logo from '../components/Logo.jsx'
import './auth.css'

const OTP_LENGTH = 6
const COUNTDOWN_SECONDS = 300 // 5 minutes, informational (server enforces expiry)
const RESEND_COOLDOWN_SECONDS = 60 // the server issues at most one code a minute
const EMPTY = ['', '', '', '', '', '']

function formatTime(total) {
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

// When a code was last requested (ms), as stored by the login page; now if unset.
function storedRequestedAt() {
  const at = Number(sessionStorage.getItem('ff_code_requested_at'))
  return Number.isFinite(at) && at > 0 ? at : Date.now()
}

// Whole seconds left of a `total`-second window that opened at `since` (ms).
function secondsRemaining(total, since, now) {
  const elapsed = Math.floor((now - since) / 1000)
  return Math.min(total, Math.max(0, total - elapsed))
}

export default function Otp() {
  const navigate = useNavigate()
  // Capture the gate once at mount: on success we clear ff_otp_pending before
  // navigating, and this prevents a stale re-guard from bouncing us to /login.
  const allowed = useRef(sessionStorage.getItem('ff_otp_pending') === '1').current
  // The address typed on /login, read once at mount for the same reason.
  const email = useRef(sessionStorage.getItem('ff_login_email') || '').current
  const inputsRef = useRef([])
  const redirectTimer = useRef(null)
  const submittingRef = useRef(false)
  const wantFocus = useRef(false)

  const [digits, setDigits] = useState(EMPTY)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [shake, setShake] = useState(false)
  const [pending, setPending] = useState(false)
  const [resending, setResending] = useState(false)
  const [requestedAt, setRequestedAt] = useState(storedRequestedAt)
  const [now, setNow] = useState(() => Date.now())

  const busy = pending || resending
  const secondsLeft = secondsRemaining(COUNTDOWN_SECONDS, requestedAt, now)
  const cooldownLeft = secondsRemaining(RESEND_COOLDOWN_SECONDS, requestedAt, now)

  // One clock drives both countdowns (informational). Counting from when the
  // code was requested keeps them right across reloads and throttled tabs.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  // Clean up any pending redirect timer on unmount.
  useEffect(() => {
    inputsRef.current[0]?.focus()
    return () => {
      if (redirectTimer.current) clearTimeout(redirectTimer.current)
    }
  }, [])

  // Re-focus the first box after a failed attempt or a new code, but only once
  // the inputs are re-enabled - focusing a still-disabled input is a no-op.
  useEffect(() => {
    if (!busy && wantFocus.current) {
      wantFocus.current = false
      inputsRef.current[0]?.focus()
    }
  }, [busy])

  function scheduleRedirect(to) {
    if (redirectTimer.current) clearTimeout(redirectTimer.current)
    redirectTimer.current = setTimeout(() => navigate(to), 1600)
  }

  function clearBoxes() {
    setDigits(EMPTY)
    // Focus is restored by the [busy] effect once the boxes are re-enabled.
    wantFocus.current = true
  }

  async function submit(code) {
    if (code.length !== OTP_LENGTH || submittingRef.current) return
    submittingRef.current = true
    setPending(true)
    setError('')
    setNotice('')
    try {
      await api.verifyOtp(code)
      sessionStorage.removeItem('ff_secret_ok')
      sessionStorage.removeItem('ff_otp_pending')
      sessionStorage.removeItem('ff_login_email')
      sessionStorage.removeItem('ff_code_requested_at')
      navigate('/dashboard')
    } catch (err) {
      const status = err && err.status
      const payload = (err && err.payload) || {}
      const kind = payload.error

      if (kind === 'locked' || status === 423) {
        sessionStorage.removeItem('ff_otp_pending')
        setError('Too many attempts. Please log in again')
        scheduleRedirect('/login')
      } else if (kind === 'no pending login') {
        // The server no longer has this sign-in (logged out or signed in in
        // another tab, cookies cleared): retrying here can never work.
        sessionStorage.removeItem('ff_otp_pending')
        setError('This sign-in has ended. Please enter your email again')
        scheduleRedirect('/login')
      } else if (kind === 'expired' && email) {
        // The "Send a new code" button below is the way forward.
        setError('That code has expired. Send a new code below')
        clearBoxes()
        setPending(false)
        submittingRef.current = false
      } else if (kind === 'expired') {
        setError('Code expired. Log in again')
        scheduleRedirect('/login')
      } else if (kind === 'invalid') {
        const remaining = payload.attempts_remaining
        setError(
          remaining != null
            ? `Incorrect code. ${remaining} attempt${remaining === 1 ? '' : 's'} remaining`
            : 'Incorrect code'
        )
        setShake(true)
        setTimeout(() => setShake(false), 400)
        clearBoxes()
        setPending(false)
        submittingRef.current = false
      } else {
        setError('Verification failed. Please try again')
        clearBoxes()
        setPending(false)
        submittingRef.current = false
      }
    }
  }

  // Ask for a fresh code for the same address. The server answers the same
  // way whether or not it is registered, so the notice stays conditional too.
  async function resend() {
    if (busy || cooldownLeft > 0 || !email) return
    setResending(true)
    setError('')
    setNotice('')
    try {
      await api.login(email)
      const at = Date.now()
      sessionStorage.setItem('ff_code_requested_at', String(at))
      setRequestedAt(at)
      setNow(at)
      clearBoxes()
      setNotice('If that email is registered, a new code is on its way.')
    } catch (err) {
      if (err && err.status === 403) {
        // The secret gate expired server-side: start over, as /login does.
        sessionStorage.removeItem('ff_secret_ok')
        navigate('/')
        return
      }
      setError('Something went wrong. Please try again.')
    }
    setResending(false)
  }

  // Back to the email form; ff_secret_ok stays so /login still lets us in.
  function changeEmail() {
    sessionStorage.removeItem('ff_otp_pending')
    navigate('/login')
  }

  function handleChange(index, rawValue) {
    const value = rawValue.replace(/\D/g, '')
    if (!value) {
      // Deletion via change - clear this box.
      const next = [...digits]
      next[index] = ''
      setDigits(next)
      return
    }
    const char = value.slice(-1)
    const next = [...digits]
    next[index] = char
    setDigits(next)
    setError('')

    if (index < OTP_LENGTH - 1) {
      inputsRef.current[index + 1]?.focus()
    }
    if (next.every((d) => d !== '')) {
      submit(next.join(''))
    }
  }

  function handleKeyDown(index, e) {
    if (e.key === 'Backspace') {
      e.preventDefault()
      const next = [...digits]
      if (next[index]) {
        next[index] = ''
        setDigits(next)
      } else if (index > 0) {
        next[index - 1] = ''
        setDigits(next)
        inputsRef.current[index - 1]?.focus()
      }
      setError('')
    } else if (e.key === 'ArrowLeft' && index > 0) {
      e.preventDefault()
      inputsRef.current[index - 1]?.focus()
    } else if (e.key === 'ArrowRight' && index < OTP_LENGTH - 1) {
      e.preventDefault()
      inputsRef.current[index + 1]?.focus()
    }
  }

  function handlePaste(e) {
    e.preventDefault()
    const text = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, OTP_LENGTH)
    if (!text) return
    const next = [...EMPTY]
    for (let i = 0; i < text.length; i++) next[i] = text[i]
    setDigits(next)
    setError('')
    const focusIndex = Math.min(text.length, OTP_LENGTH - 1)
    inputsRef.current[focusIndex]?.focus()
    if (text.length === OTP_LENGTH) submit(text)
  }

  if (!allowed) return <Navigate to="/login" replace />

  const expired = secondsLeft <= 0

  let resendLabel = 'Send a new code'
  if (resending) resendLabel = 'Sending…'
  else if (cooldownLeft > 0) resendLabel = `Send a new code in ${formatTime(cooldownLeft)}`

  return (
    <div className="ff-brand ff-admin ff-auth">
      <div className="ff-auth__card">
        <div className="ff-auth__logo">
          <Link to="/" aria-label="Farmers Forward home"><Logo variant="dashboard" /></Link>
        </div>

        <p className="ff-eyebrow ff-auth__eyebrow">One more little step.</p>
        <h1 className="ff-auth__title">Verify your identity</h1>
        <p className="ff-auth__subtitle">
          If {email ? <strong>{email}</strong> : 'that email'} is registered, we&apos;ve
          sent a 6-digit code to it.
        </p>

        {error && (
          <div className="ff-auth__error" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <div className="ff-auth__error ff-auth__error--notice" role="status">
            {notice}
          </div>
        )}

        <div className="ff-otp" onPaste={handlePaste}>
          {digits.map((d, i) => (
            <input
              key={i}
              ref={(el) => (inputsRef.current[i] = el)}
              className={
                'ff-otp__box' +
                (d ? ' ff-otp__box--filled' : '') +
                (shake ? ' ff-otp__box--error' : '')
              }
              type="text"
              inputMode="numeric"
              autoComplete={i === 0 ? 'one-time-code' : 'off'}
              maxLength={1}
              value={d}
              disabled={busy}
              aria-label={`Digit ${i + 1}`}
              onChange={(e) => handleChange(i, e.target.value)}
              onKeyDown={(e) => handleKeyDown(i, e)}
              onFocus={(e) => e.target.select()}
            />
          ))}
        </div>

        <div className={'ff-otp__timer' + (expired ? ' ff-otp__timer--warn' : '')}>
          {expired ? (
            <span>
              {email
                ? 'Code expired. Send a new code below.'
                : 'Code expired. Request a new one by logging in again.'}
            </span>
          ) : (
            <span>
              Code expires in <strong>{formatTime(secondsLeft)}</strong>
            </span>
          )}
        </div>

        <div className="ff-otp__resend">
          {email && (
            <button
              type="button"
              className="ff-btn ff-btn--ghost ff-btn--sm"
              disabled={busy || cooldownLeft > 0}
              onClick={resend}
            >
              {resendLabel}
            </button>
          )}
          <button
            type="button"
            className="ff-btn ff-btn--ghost ff-btn--sm"
            disabled={busy}
            onClick={changeEmail}
          >
            Use a different email
          </button>
        </div>

        <div className="ff-auth__actions">
          <button
            type="button"
            className="ff-btn ff-btn--primary"
            disabled={busy || digits.some((d) => d === '')}
            onClick={() => submit(digits.join(''))}
          >
            {pending ? 'Verifying…' : 'Verify code'}
          </button>
        </div>

        <p className="ff-auth__footer">Protected area. Authorized personnel only.</p>
      </div>
    </div>
  )
}
