import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import api from '../api/client.js'
import Logo from '../components/Logo.jsx'
import './auth.css'

const OTP_LENGTH = 6
const COUNTDOWN_SECONDS = 300 // 5 minutes, informational (server enforces expiry)
const EMPTY = ['', '', '', '', '', '']

function formatTime(total) {
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function Otp() {
  const navigate = useNavigate()
  // Capture the gate once at mount: on success we clear ff_otp_pending before
  // navigating, and this prevents a stale re-guard from bouncing us to /login.
  const allowed = useRef(sessionStorage.getItem('ff_otp_pending') === '1').current
  const inputsRef = useRef([])
  const redirectTimer = useRef(null)
  const submittingRef = useRef(false)
  const wantFocus = useRef(false)

  const [digits, setDigits] = useState(EMPTY)
  const [error, setError] = useState('')
  const [shake, setShake] = useState(false)
  const [pending, setPending] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState(COUNTDOWN_SECONDS)

  // Countdown timer (informational).
  useEffect(() => {
    const t = setInterval(() => {
      setSecondsLeft((s) => (s <= 0 ? 0 : s - 1))
    }, 1000)
    return () => clearInterval(t)
  }, [])

  // Clean up any pending redirect timer on unmount.
  useEffect(() => {
    inputsRef.current[0]?.focus()
    return () => {
      if (redirectTimer.current) clearTimeout(redirectTimer.current)
    }
  }, [])

  // Re-focus the first box after a failed attempt, but only once the inputs are
  // re-enabled - focusing a still-disabled input is a no-op.
  useEffect(() => {
    if (!pending && wantFocus.current) {
      wantFocus.current = false
      inputsRef.current[0]?.focus()
    }
  }, [pending])

  function scheduleRedirect(to) {
    if (redirectTimer.current) clearTimeout(redirectTimer.current)
    redirectTimer.current = setTimeout(() => navigate(to), 1600)
  }

  function clearBoxes() {
    setDigits(EMPTY)
    // Focus is restored by the [pending] effect once the boxes are re-enabled.
    wantFocus.current = true
  }

  async function submit(code) {
    if (code.length !== OTP_LENGTH || submittingRef.current) return
    submittingRef.current = true
    setPending(true)
    setError('')
    try {
      await api.verifyOtp(code)
      sessionStorage.removeItem('ff_secret_ok')
      sessionStorage.removeItem('ff_otp_pending')
      navigate('/dashboard')
    } catch (err) {
      const status = err && err.status
      const payload = (err && err.payload) || {}
      const kind = payload.error

      if (kind === 'locked' || status === 423) {
        sessionStorage.removeItem('ff_otp_pending')
        setError('Too many attempts. Please log in again')
        scheduleRedirect('/login')
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

  return (
    <div className="ff-brand ff-admin ff-auth">
      <div className="ff-auth__card">
        <div className="ff-auth__logo">
          <Link to="/" aria-label="Farmers Forward home"><Logo variant="dashboard" /></Link>
        </div>

        <p className="ff-eyebrow ff-auth__eyebrow">One more little step.</p>
        <h1 className="ff-auth__title">Verify your identity</h1>
        <p className="ff-auth__subtitle">
          Enter the 6-digit code we sent to your email.
        </p>

        {error && (
          <div className="ff-auth__error" role="alert">
            {error}
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
              disabled={pending}
              aria-label={`Digit ${i + 1}`}
              onChange={(e) => handleChange(i, e.target.value)}
              onKeyDown={(e) => handleKeyDown(i, e)}
              onFocus={(e) => e.target.select()}
            />
          ))}
        </div>

        <div className={'ff-otp__timer' + (expired ? ' ff-otp__timer--warn' : '')}>
          {expired ? (
            <span>Code expired. Request a new one by logging in again.</span>
          ) : (
            <span>
              Code expires in <strong>{formatTime(secondsLeft)}</strong>
            </span>
          )}
        </div>

        <div className="ff-auth__actions">
          <button
            type="button"
            className="ff-btn ff-btn--primary"
            disabled={pending || digits.some((d) => d === '')}
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
