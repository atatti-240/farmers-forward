import { useState } from 'react'
import api from '../../api/client.js'
import { SquiggleUnderline } from './doodles.jsx'

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

const EMPTY = { name: '', email: '', message: '' }

export default function Contact() {
  const [form, setForm] = useState(EMPTY)
  const [errors, setErrors] = useState({})
  const [status, setStatus] = useState('idle') // idle | submitting | success | error
  const [serverError, setServerError] = useState('')

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }))
    if (errors[field]) {
      setErrors((prev) => ({ ...prev, [field]: undefined }))
    }
  }

  function validate() {
    const next = {}
    if (!form.name.trim()) next.name = 'Please tell us your name.'
    if (!form.email.trim()) {
      next.email = 'Please enter your email.'
    } else if (!EMAIL_RE.test(form.email.trim())) {
      next.email = 'That email doesn’t look right.'
    }
    if (!form.message.trim()) next.message = 'Please add a short message.'
    return next
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setServerError('')
    const found = validate()
    setErrors(found)
    if (Object.keys(found).length > 0) return

    setStatus('submitting')
    try {
      await api.sendContact({
        name: form.name.trim(),
        email: form.email.trim(),
        message: form.message.trim(),
      })
      setStatus('success')
      setForm(EMPTY)
    } catch (err) {
      setStatus('error')
      const msg =
        (err && err.payload && err.payload.error) ||
        'Something went wrong sending your message. Please try again.'
      setServerError(msg)
    }
  }

  const submitting = status === 'submitting'

  return (
    <section id="contact" className="ff-section ff-contact">
      <div className="ff-container ff-contact__grid">
        <div className="ff-contact__intro">
          <span className="ff-eyebrow">say hello</span>
          <h2 className="ff-contact__title">Tell us about your field.</h2>
          <SquiggleUnderline className="ff-contact__squiggle" />
          <p className="ff-contact__body">
            A few sentences is plenty: where you farm, what you grow, and
            what&rsquo;s been bothering you about the land. We&rsquo;ll write back
            with next steps for your free assessment.
          </p>
          <a className="ff-contact__email" href="mailto:farmersforward2026@gmail.com">
            farmersforward2026@gmail.com
          </a>
          <p className="ff-contact__reassure">
            Prefer plain email? Write to us directly. A real person reads
            every message.
          </p>
        </div>

        <div className="ff-card ff-contact__card">
          {status === 'success' ? (
            <div className="ff-contact__success" role="status">
              <span className="ff-contact__success-icon" aria-hidden="true">✓</span>
              <h3 className="ff-card__title">Got it, thank you!</h3>
              <p>
                Your note landed in our inbox. We&rsquo;ll write back soon to set
                up your free assessment.
              </p>
              <button
                type="button"
                className="ff-btn ff-btn--secondary"
                onClick={() => setStatus('idle')}
              >
                Send another message
              </button>
            </div>
          ) : (
            <form className="ff-contact__form" onSubmit={handleSubmit} noValidate>
              <div className="ff-field">
                <label className="ff-label" htmlFor="ff-contact-name">
                  Your name
                </label>
                <input
                  id="ff-contact-name"
                  className={
                    'ff-input' + (errors.name ? ' ff-input--error' : '')
                  }
                  type="text"
                  value={form.name}
                  onChange={(e) => update('name', e.target.value)}
                  disabled={submitting}
                  autoComplete="name"
                />
                {errors.name && (
                  <span className="ff-error-text">{errors.name}</span>
                )}
              </div>

              <div className="ff-field">
                <label className="ff-label" htmlFor="ff-contact-email">
                  Your email
                </label>
                <input
                  id="ff-contact-email"
                  className={
                    'ff-input' + (errors.email ? ' ff-input--error' : '')
                  }
                  type="email"
                  value={form.email}
                  onChange={(e) => update('email', e.target.value)}
                  disabled={submitting}
                  autoComplete="email"
                />
                {errors.email && (
                  <span className="ff-error-text">{errors.email}</span>
                )}
              </div>

              <div className="ff-field">
                <label className="ff-label" htmlFor="ff-contact-message">
                  Your farm, in a few words
                </label>
                <textarea
                  id="ff-contact-message"
                  className={
                    'ff-input ff-contact__textarea' +
                    (errors.message ? ' ff-input--error' : '')
                  }
                  rows={4}
                  value={form.message}
                  onChange={(e) => update('message', e.target.value)}
                  disabled={submitting}
                  placeholder="e.g. Twelve acres off FM 762, mostly vegetables. Yields have been slipping the last two seasons."
                />
                {errors.message && (
                  <span className="ff-error-text">{errors.message}</span>
                )}
              </div>

              {status === 'error' && serverError && (
                <p className="ff-error-text ff-contact__server-error" role="alert">
                  {serverError}
                </p>
              )}

              <button
                type="submit"
                className="ff-btn ff-btn--primary ff-contact__submit"
                disabled={submitting}
              >
                {submitting ? 'Sending…' : 'Send it over'}
              </button>
            </form>
          )}
        </div>
      </div>
    </section>
  )
}
