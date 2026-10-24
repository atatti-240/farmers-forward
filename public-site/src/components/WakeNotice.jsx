/* ==========================================================================
   Farmers Forward (public site) - WakeNotice
   The free server sleeps when nobody is using it and needs up to a minute to
   wake. When any request has been waiting more than 3 s (api/client.js), this
   shows a "Waking up the soil lab…" panel with the time so far, so the page
   never just looks stuck. It disappears the moment the server answers.
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
  const clock = `0:${String(Math.min(seconds, 59)).padStart(2, '0')}${seconds > 59 ? '+' : ''}`

  return (
    <div className="wake" role="status" aria-live="polite">
      <svg className="wake__art" viewBox="0 0 56 56" aria-hidden="true">
        <path className="wake__layer" d="M4 22c8-2 16 2 24 0s16-2 24 0" />
        <path className="wake__layer" d="M4 34c8-2 16 2 24 0s16-2 24 0" />
        <path className="wake__layer" d="M4 46c8-2 16 2 24 0s16-2 24 0" />
        <g className="wake__probe">
          <path d="M28 4v30" />
          <path d="M24 4h8" />
        </g>
        <circle className="wake__ping" cx="28" cy="40" r="3" />
      </svg>
      <div>
        <p className="wake__title">Waking up the soil lab…</p>
        <p className="wake__sub">
          Our free server naps when nobody’s using it. The first request can
          take up to a minute, and we’ll keep trying.
          <span className="wake__time">{clock}</span>
        </p>
      </div>
    </div>
  )
}
