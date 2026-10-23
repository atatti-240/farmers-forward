/* ==========================================================================
   Farmers Forward - Admins page (owner only)
   Everyone who can sign in to the field notebook. An owner adds an admin by
   email + display name, and deactivates or reactivates anyone but themselves;
   nobody is ever deleted. Guarded by `<RequireAuth ownerOnly>` in App.jsx.
   ========================================================================== */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import adminsApi from '../api/admins.js'
import { useAdmin } from '../components/AdminContext.jsx'
import AdminNav from '../components/AdminNav.jsx'
import Spinner from '../components/Spinner.jsx'
import '../dashboard/dashboard.css'
import '../dashboard/admin.css'

const NAME_MAX = 100
const EMPTY = { email: '', display_name: '' }
// 'owner' and 'admin' are the only roles today; anything newer shows as sent.
const ROLE_LABELS = { owner: 'Owner', admin: 'Admin' }

/** Client-side checks only; the server validates both fields again. */
function validate(form) {
  const errors = {}
  if (!form.email.trim()) errors.email = 'Please enter their email address.'
  const name = form.display_name.trim()
  if (!name) {
    errors.display_name = 'Please add a display name.'
  } else if (Array.from(name).length > NAME_MAX) {
    // Counted in characters, as the server counts them, not UTF-16 units.
    errors.display_name = `Keep the display name to ${NAME_MAX} characters or fewer.`
  }
  return errors
}

/**
 * RequireAuth checked the session on the way in, so a 401 or 403 means it has
 * changed since (logged out, deactivated, or no longer an owner). Leaves the
 * page in that case and returns true.
 */
function leftAfterRefusal(err, navigate) {
  const status = err && err.status
  if (status === 401) navigate('/login', { replace: true })
  else if (status === 403) navigate('/dashboard', { replace: true })
  else return false
  return true
}

/** An email that, in a narrow column, wraps after the @ before anywhere else. */
function Email({ value }) {
  const email = String(value)
  const at = email.lastIndexOf('@')
  if (at <= 0) return email
  return (
    <>
      {email.slice(0, at + 1)}
      <wbr />
      {email.slice(at + 1)}
    </>
  )
}

/** An admin's last login as a local date with the time under it, or 'Never'. */
function LastLogin({ value }) {
  if (!value) return <span className="ff-text-muted">Never</span>
  // The server sends microseconds; Date (and <time dateTime>) only promise
  // to read milliseconds.
  const iso = String(value).replace(/(\.\d{3})\d+/, '$1')
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return value
  return (
    <time dateTime={iso}>
      {when.toLocaleDateString(undefined, { dateStyle: 'medium' })}
      <span className="ff-admins__time">
        {when.toLocaleTimeString(undefined, { timeStyle: 'short' })}
      </span>
    </time>
  )
}

export default function Admins() {
  const navigate = useNavigate()
  const me = useAdmin()
  const emailRef = useRef(null)
  const nameRef = useRef(null)
  const wantFocus = useRef(false)

  // null while loading, then every admin, oldest first
  const [admins, setAdmins] = useState(null)
  const [loadError, setLoadError] = useState('')
  // Server-side email problems (e.g. RESEND_FROM still Resend's test sender)
  // that would stop new admins from ever receiving a code.
  const [warnings, setWarnings] = useState([])
  // Bumped by "Try again" to re-run the load.
  const [attempt, setAttempt] = useState(0)
  // { [adminId]: true } while that row's deactivate/reactivate is in flight
  const [rowPending, setRowPending] = useState({})
  const [rowError, setRowError] = useState('')

  const [form, setForm] = useState(EMPTY)
  const [errors, setErrors] = useState({})
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState('')
  const [added, setAdded] = useState(null)

  useEffect(() => {
    let active = true
    adminsApi
      .listAdmins()
      .then((res) => {
        if (!active) return
        setAdmins(Array.isArray(res && res.admins) ? res.admins : [])
        setWarnings(Array.isArray(res && res.warnings) ? res.warnings : [])
      })
      .catch((err) => {
        if (!active || leftAfterRefusal(err, navigate)) return
        setLoadError('The list of admins didn’t load. Check your connection and try again.')
      })
    return () => {
      active = false
    }
  }, [attempt, navigate])

  // Back to the email box once the form is re-enabled after a request, ready
  // for the next admin or a fix (focusing a still-disabled input is a no-op).
  useEffect(() => {
    if (!adding && wantFocus.current) {
      wantFocus.current = false
      emailRef.current?.focus()
    }
  }, [adding])

  function retry() {
    setLoadError('')
    setAttempt((n) => n + 1)
  }

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }))
    if (errors[field]) {
      setErrors((prev) => ({ ...prev, [field]: undefined }))
    }
    setAddError('')
    setAdded(null)
  }

  async function handleAdd(e) {
    e.preventDefault()
    if (adding) return
    setAddError('')
    setAdded(null)
    const found = validate(form)
    setErrors(found)
    if (found.email || found.display_name) {
      const first = found.email ? emailRef : nameRef
      first.current?.focus()
      return
    }

    setAdding(true)
    try {
      const res = await adminsApi.createAdmin({
        email: form.email.trim(),
        display_name: form.display_name.trim(),
      })
      const admin = res && res.admin
      if (admin) {
        // Newest last, like the server's oldest-first list.
        setAdmins((prev) =>
          prev && !prev.some((a) => a.id === admin.id) ? [...prev, admin] : prev
        )
      }
      setAdded(admin || { display_name: form.display_name.trim(), email: form.email.trim() })
      setForm(EMPTY)
    } catch (err) {
      if (leftAfterRefusal(err, navigate)) return
      const status = err && err.status
      setAddError(
        ((status === 400 || status === 409) && err.payload && err.payload.error) ||
          'That admin couldn’t be added. Please try again.'
      )
    }
    wantFocus.current = true
    setAdding(false)
  }

  async function toggleActive(admin) {
    if (rowPending[admin.id]) return
    const activate = !admin.is_active
    if (
      !activate &&
      !window.confirm(
        `Deactivate ${admin.display_name}? They’ll be signed out right away and can’t sign in until you reactivate them.`
      )
    ) {
      return
    }
    setRowError('')
    setRowPending((prev) => ({ ...prev, [admin.id]: true }))
    try {
      const res = await adminsApi.setAdminActive(admin.id, activate)
      const updated = (res && res.admin) || { ...admin, is_active: activate }
      setAdmins((prev) => prev && prev.map((a) => (a.id === admin.id ? updated : a)))
    } catch (err) {
      if (leftAfterRefusal(err, navigate)) return
      setRowError(
        (err && err.status === 400 && err.payload && err.payload.error) ||
          `${admin.display_name} couldn’t be ${activate ? 'reactivated' : 'deactivated'}. Please try again.`
      )
    }
    setRowPending((prev) => {
      const next = { ...prev }
      delete next[admin.id]
      return next
    })
  }

  const activeCount = admins ? admins.filter((a) => a.is_active).length : 0

  return (
    <div className="ff-brand ff-admin ff-dash">
      <AdminNav />

      <main className="ff-dash__main">
        <p className="ff-eyebrow">Who can open the notebook.</p>
        <div className="ff-step-head">
          <h1>Admins</h1>
          <p>
            Everyone here signs in with a one-time code sent to their email.
            Deactivating someone signs them out right away. Nothing is deleted,
            so you can reactivate them any time.
          </p>
        </div>

        {warnings.map((warning) => (
          <p key={warning} className="ff-admins__warning" role="status">
            <strong>Login emails can't reach everyone yet.</strong> {warning}
          </p>
        ))}

        {/* Add an admin */}
        <section className="ff-card ff-admins__add">
          <h2 className="ff-card__title">Add an admin</h2>
          <p className="ff-hint ff-mt-0">
            Use the email address they&rsquo;ll sign in with. Their display name
            shows in the header once they&rsquo;re in.
          </p>
          <form onSubmit={handleAdd} noValidate>
            <div className="ff-admins__fields">
              <div className="ff-field">
                <label className="ff-label" htmlFor="ff-admin-email">
                  Email
                </label>
                <input
                  id="ff-admin-email"
                  ref={emailRef}
                  className={'ff-input' + (errors.email ? ' ff-input--error' : '')}
                  type="email"
                  value={form.email}
                  placeholder="e.g. rosa@example.org"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  disabled={adding}
                  aria-invalid={errors.email ? true : undefined}
                  aria-describedby={errors.email ? 'ff-admin-email-error' : undefined}
                  onChange={(e) => update('email', e.target.value)}
                />
                {errors.email && (
                  <span id="ff-admin-email-error" className="ff-error-text">
                    {errors.email}
                  </span>
                )}
              </div>
              <div className="ff-field">
                <label className="ff-label" htmlFor="ff-admin-name">
                  Display name
                </label>
                <input
                  id="ff-admin-name"
                  ref={nameRef}
                  className={'ff-input' + (errors.display_name ? ' ff-input--error' : '')}
                  type="text"
                  value={form.display_name}
                  placeholder="e.g. Rosa Garza"
                  autoComplete="off"
                  disabled={adding}
                  aria-invalid={errors.display_name ? true : undefined}
                  aria-describedby={errors.display_name ? 'ff-admin-name-error' : undefined}
                  onChange={(e) => update('display_name', e.target.value)}
                />
                {errors.display_name && (
                  <span id="ff-admin-name-error" className="ff-error-text">
                    {errors.display_name}
                  </span>
                )}
              </div>
            </div>

            <div className="ff-admins__submit">
              <button type="submit" className="ff-btn ff-btn--primary" disabled={adding}>
                {adding ? 'Adding…' : 'Add admin'}
              </button>
              <p className="ff-admins__added" role="status">
                {added && (
                  <>
                    Added {added.display_name}. They can sign in with{' '}
                    <strong>{added.email}</strong>
                    {warnings.length ? ' once login emails can reach them (see the note above).' : ' right away.'}
                  </>
                )}
              </p>
            </div>
            {addError && (
              <p className="ff-error-text ff-admins__msg" role="alert">
                {addError}
              </p>
            )}
          </form>
        </section>

        {/* Everyone with access */}
        <section>
          <div className="ff-inline ff-inline--between ff-admins__head">
            <h2 id="ff-admins-title" className="ff-card__title">
              Everyone with access
            </h2>
            {admins && admins.length > 0 && (
              <span className="ff-toolbar__label">
                {admins.length} admin{admins.length === 1 ? '' : 's'} · {activeCount} active
              </span>
            )}
          </div>

          {loadError ? (
            <div className="ff-empty">
              <p role="alert">{loadError}</p>
              <button type="button" className="ff-btn ff-btn--secondary ff-btn--sm" onClick={retry}>
                Try again
              </button>
            </div>
          ) : admins === null ? (
            <div className="ff-admins__loading">
              <Spinner label="Loading admins" />
            </div>
          ) : (
            <div
              className="ff-table-wrap ff-card ff-admins__table"
              role="region"
              aria-labelledby="ff-admins-title"
              tabIndex={0}
            >
              <table className="ff-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Last login</th>
                    <th>
                      <span className="ff-admins__sr">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {admins.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="ff-text-center ff-text-muted">
                        No admins yet.
                      </td>
                    </tr>
                  ) : (
                    admins.map((a) => {
                      const self = Boolean(me && a.id === me.id)
                      const pending = Boolean(rowPending[a.id])
                      return (
                        <tr key={a.id} className={a.is_active ? undefined : 'ff-admins__row--off'}>
                          <th scope="row" className="ff-admins__name">
                            {a.display_name}
                            {/* The no-break space keeps the marker with the name. */}
                            {self && <>&nbsp;<span className="ff-admins__you">You</span></>}
                          </th>
                          <td className="ff-admins__email">
                            <Email value={a.email} />
                          </td>
                          <td>
                            <span className={`ff-badge${a.is_active ? '' : ' ff-badge--muted'}`}>
                              {ROLE_LABELS[a.role] || a.role}
                            </span>
                          </td>
                          <td>
                            <span className={`ff-badge ${a.is_active ? 'ff-badge--green' : 'ff-badge--red'}`}>
                              {a.is_active ? 'Active' : 'Deactivated'}
                            </span>
                          </td>
                          <td className="ff-admins__seen">
                            <LastLogin value={a.last_login_at} />
                          </td>
                          <td className="ff-admins__act">
                            {/* aria-disabled, not disabled, so keyboard focus stays
                                on the button while its request is out. */}
                            {!self && (
                              <button
                                type="button"
                                className={`ff-btn ff-btn--sm ${a.is_active ? 'ff-btn--danger' : 'ff-btn--secondary'}`}
                                aria-disabled={pending || undefined}
                                onClick={() => toggleActive(a)}
                              >
                                {a.is_active
                                  ? pending ? 'Deactivating…' : 'Deactivate'
                                  : pending ? 'Reactivating…' : 'Reactivate'}
                              </button>
                            )}
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}
          {rowError && (
            <p className="ff-error-text ff-admins__msg" role="alert">
              {rowError}
            </p>
          )}
        </section>
      </main>
      <footer className="ff-dash__footer">Farmers Forward · Rooted in healthier soil.</footer>
    </div>
  )
}
