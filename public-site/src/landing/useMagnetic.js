/* Magnetic hover for the main call to action: the button leans toward the
   pointer when it's close, and springs back when it leaves. Off for touch
   and for prefers-reduced-motion. */

import { useEffect } from 'react'

export default function useMagnetic(ref, { strength = 0.3, reach = 70 } = {}) {
  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const fine = window.matchMedia?.('(hover: hover) and (pointer: fine)').matches
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (!fine || reduce) return undefined

    let frame = 0
    const move = (event) => {
      const r = el.getBoundingClientRect()
      const cx = r.left + r.width / 2
      const cy = r.top + r.height / 2
      const dx = event.clientX - cx
      const dy = event.clientY - cy
      const near = Math.abs(dx) < r.width / 2 + reach && Math.abs(dy) < r.height / 2 + reach
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        el.style.transform = near ? `translate(${dx * strength}px, ${dy * strength}px)` : ''
      })
    }
    const leave = () => {
      cancelAnimationFrame(frame)
      el.style.transform = ''
    }
    el.style.transition = 'transform 0.5s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.35s, background-color 0.25s'
    window.addEventListener('pointermove', move, { passive: true })
    document.addEventListener('pointerleave', leave)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('pointermove', move)
      document.removeEventListener('pointerleave', leave)
      el.style.transform = ''
    }
  }, [ref, strength, reach])
}
