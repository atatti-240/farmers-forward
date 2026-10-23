/* ==========================================================================
   Farmers Forward (public site) - WakeNotice
   The free server sleeps when nobody is using it and needs up to a minute to
   wake. When any request has been waiting more than 3 s (api/client.js), this
   shows an animated "Waking up the soil lab…" panel with the time so far, so
   the page never just looks stuck. It disappears the moment the server
   answers.
   ========================================================================== */

import { useEffect, useState } from 'react'

import { subscribeWaking } from '../api/client.js'

export default function WakeNotice() {
  const [waking, setWaking] = useState(false)
  const [since, setSince] = useState(0)
  const [now, setNow] = useState(0)

  useEffect(() => subscribeWaking((on) => {
    setWaking(on)
    if (on) {
      const t = Date.now()
      setSince(t)
      setNow(t)
    }
  }), [])

  useEffect(() => {
    if (!waking) return undefined
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [waking])

  if (!waking) return null
  // The 3 s before the panel appeared count too.
  const seconds = Math.round((now - since) / 1000) + 3

  return (
    <div className="wake" role="status" aria-live="polite">
      <div className="wake__art" aria-hidden="true">
        <span className="wake__ring" />
        <span className="wake__ring wake__ring--2" />
        <svg viewBox="0 0 48 48" className="wake__sprout">
          <path d="M24 42V22" />
          <path d="M24 28c-7 0-11-4-12-11 7 0 11 4 12 11z" />
          <path d="M24 24c1-7 5-11 12-11-1 7-5 11-12 11z" />
        </svg>
      </div>
      <div className="wake__text">
        <p className="wake__title">Waking up the soil lab…</p>
        <p className="wake__sub">
          Our free server naps when it’s idle. The first request can take up
          to a minute. Hang tight, we’ll keep trying. <span className="wake__time">{seconds}s</span>
        </p>
      </div>
    </div>
  )
}
