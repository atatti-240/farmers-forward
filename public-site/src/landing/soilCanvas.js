/* ==========================================================================
   Farmers Forward (public site) - living soil canvas
   The hero's bottom band, drawn as a soil cross-section under the field
   footage: a faint surface line, root systems that grow down from it, and
   drifting mineral/microbe particles. Everything brightens as `score` climbs
   (the hero counts an example reading's score up from 0), so the soil
   literally comes alive as the number rises.

   Plain canvas 2D, no dependencies. Roots are generated once per size and
   drawn progressively; once grown they're cached to an offscreen canvas so a
   frame is one drawImage plus the particles. The loop pauses when the hero
   is off screen or the tab is hidden. Under prefers-reduced-motion the final
   state is drawn once and nothing moves.
   ========================================================================== */

const TAU = Math.PI * 2

/** Small seeded PRNG so the roots look the same on every visit. */
function mulberry32(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * Branching root segments, each with the order it grows in (`t`, 0..1).
 * @returns {{x1:number,y1:number,x2:number,y2:number,w:number,t:number,tip:boolean}[]}
 */
function growRoots(width, surfaceY, depth, count, rand) {
  const segs = []
  let order = 0
  const step = Math.max(5, Math.min(9, width / 180))

  function branch(x, y, angle, len, w, gen) {
    let cx = x
    let cy = y
    let a = angle
    for (let i = 0; i < len; i++) {
      a += (rand() - 0.5) * 0.42
      // Gravity: keep heading down, never back up through the surface.
      a += (Math.PI / 2 - a) * 0.08
      const nx = cx + Math.cos(a) * step
      const ny = Math.min(surfaceY + depth, cy + Math.sin(a) * step)
      segs.push({ x1: cx, y1: cy, x2: nx, y2: ny, w, t: order++, tip: i === len - 1 })
      cx = nx
      cy = ny
      if (gen < 4 && rand() < 0.085 - gen * 0.012) {
        const side = rand() < 0.5 ? -1 : 1
        branch(cx, cy, a + side * (0.5 + rand() * 0.7), Math.floor(len * (0.35 + rand() * 0.4)), w * 0.62, gen + 1)
      }
      if (cy >= surfaceY + depth - 1) break
    }
  }

  // Plants in loose clumps at irregular spacing: a few deep taproots, more
  // shallow fibrous ones.
  let x = rand() * (width / count)
  while (x < width) {
    const deep = rand() < 0.4
    const len = Math.floor((depth / step) * (deep ? 0.8 + rand() * 0.35 : 0.3 + rand() * 0.35))
    const stems = deep ? 1 : 2 + Math.floor(rand() * 3)
    for (let k = 0; k < stems; k++) {
      branch(x + (rand() - 0.5) * 10, surfaceY, Math.PI / 2 + (rand() - 0.5) * (deep ? 0.35 : 1.3),
        Math.floor(len * (0.6 + rand() * 0.4)), deep ? 1.7 + rand() * 0.5 : 0.9 + rand() * 0.5, deep ? 0 : 1)
    }
    x += (width / count) * (0.45 + rand() * 1.3)
  }
  for (const s of segs) s.t /= Math.max(1, order - 1)
  // Grow every root at once rather than one after another: re-time each
  // segment by its depth below the surface.
  for (const s of segs) s.t = Math.min(1, ((s.y1 - surfaceY) / depth) * 0.85 + s.t * 0.15)
  return segs
}

export default class SoilCanvas {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{ band?: number, reduced?: boolean }} [opts] - `band`: share of
   *   the canvas height (from the bottom) that is soil.
   */
  constructor(canvas, { band = 0.3, reduced = false } = {}) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d')
    this.band = band
    this.reduced = reduced
    this.score = reduced ? 1 : 0       // 0..1, set by the hero's count-up
    this.growth = reduced ? 1 : 0      // follows score, eased
    this.pointer = { x: -1e4, y: -1e4 }
    this.running = false
    this.frame = 0
    this.lastT = 0
    this.cache = null
    this.resize = this.resize.bind(this)
    this.tick = this.tick.bind(this)
    this.resize()
  }

  resize() {
    const { canvas } = this
    const rect = canvas.getBoundingClientRect()
    const dpr = Math.min(window.devicePixelRatio || 1, 1.75)
    this.w = Math.max(1, rect.width)
    this.h = Math.max(1, rect.height)
    this.dpr = dpr
    canvas.width = Math.round(this.w * dpr)
    canvas.height = Math.round(this.h * dpr)
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    const rand = mulberry32(29449)
    this.surfaceY = this.h * (1 - this.band)
    this.depth = this.h - this.surfaceY
    const roots = this.w < 700 ? 6 : Math.round(this.w / 130)
    this.segs = growRoots(this.w, this.surfaceY, this.depth * 0.96, roots, rand)

    const n = this.w < 700 ? 70 : 150
    this.particles = Array.from({ length: n }, () => {
      const inSoil = rand() < 0.82
      return {
        x: rand() * this.w,
        y: inSoil ? this.surfaceY + rand() * this.depth : this.surfaceY - rand() * this.h * 0.25,
        r: 0.6 + rand() * 1.6,
        vx: (rand() - 0.5) * 0.12,
        vy: (rand() - 0.5) * 0.08 - (inSoil ? 0 : 0.05),
        phase: rand() * TAU,
        blue: rand() < 0.28,
        soil: inSoil,
      }
    })
    this.cache = null
    this.draw(0)
  }

  setScore(fraction) {
    this.score = Math.max(0, Math.min(1, fraction))
    if (!this.running) this.draw(0)
  }

  setPointer(x, y) {
    this.pointer.x = x
    this.pointer.y = y
  }

  start() {
    if (this.running || this.reduced) return
    this.running = true
    this.lastT = performance.now()
    this.frame = requestAnimationFrame(this.tick)
  }

  stop() {
    this.running = false
    cancelAnimationFrame(this.frame)
  }

  destroy() {
    this.stop()
  }

  tick(now) {
    if (!this.running) return
    const dt = Math.min(48, now - this.lastT)
    this.lastT = now
    this.draw(dt)
    this.frame = requestAnimationFrame(this.tick)
  }

  /** Roots up to `growth`, into ctx. Segments are batched into a few
   *  strokes (by width and depth band) rather than one stroke each. */
  drawRoots(ctx, growth, light) {
    const BANDS = 6
    const paths = new Map()
    for (const s of this.segs) {
      if (s.t > growth) continue
      const band = Math.min(BANDS - 1, Math.floor(((s.y1 - this.surfaceY) / this.depth) * BANDS))
      const width = s.w > 1.4 ? 2 : s.w > 0.9 ? 1 : 0
      const key = band * 3 + width
      let path = paths.get(key)
      if (!path) {
        path = new Path2D()
        paths.set(key, path)
      }
      path.moveTo(s.x1, s.y1)
      path.lineTo(s.x2, s.y2)
    }
    ctx.lineCap = 'round'
    for (const [key, path] of paths) {
      const band = Math.floor(key / 3)
      const width = key % 3
      const depthFade = 1 - (band + 0.5) / (BANDS * 1.15)
      ctx.strokeStyle = `rgba(214, 226, 255, ${(0.1 + 0.42 * light) * depthFade})`
      ctx.lineWidth = width === 2 ? 1.8 : width === 1 ? 1.15 : 0.7
      ctx.stroke(path)
    }
    // Growing tips glow blue.
    const tips = new Path2D()
    for (const s of this.segs) {
      if (!s.tip || s.t > growth) continue
      tips.moveTo(s.x2 + 1.5, s.y2)
      tips.arc(s.x2, s.y2, 1.5, 0, TAU)
    }
    ctx.fillStyle = `rgba(61, 123, 255, ${0.35 + 0.55 * light})`
    ctx.fill(tips)
  }

  draw(dt) {
    const { ctx, w, h } = this
    // Ease root growth toward the score so the two move together.
    this.growth += (this.score - this.growth) * (dt ? Math.min(1, dt / 260) : 1)
    const light = this.growth

    ctx.clearRect(0, 0, w, h)

    // Soil body: darkens from the surface down.
    const soil = ctx.createLinearGradient(0, this.surfaceY - 40, 0, h)
    soil.addColorStop(0, 'rgba(5, 7, 13, 0)')
    soil.addColorStop(0.18, 'rgba(5, 7, 13, 0.72)')
    soil.addColorStop(1, 'rgba(5, 7, 13, 0.96)')
    ctx.fillStyle = soil
    ctx.fillRect(0, this.surfaceY - 40, w, h - this.surfaceY + 40)

    // Faint horizon bands (soil horizons).
    ctx.lineWidth = 1
    for (let i = 1; i <= 3; i++) {
      const y = this.surfaceY + this.depth * (i / 4)
      ctx.strokeStyle = `rgba(233, 238, 248, ${0.035 + 0.02 * light})`
      ctx.setLineDash([2, 6])
      ctx.beginPath()
      ctx.moveTo(0, y)
      ctx.lineTo(w, y)
      ctx.stroke()
    }
    ctx.setLineDash([])

    // The surface line.
    const surf = ctx.createLinearGradient(0, 0, w, 0)
    surf.addColorStop(0, 'rgba(233, 238, 248, 0)')
    surf.addColorStop(0.5, `rgba(233, 238, 248, ${0.25 + 0.35 * light})`)
    surf.addColorStop(1, 'rgba(233, 238, 248, 0)')
    ctx.strokeStyle = surf
    ctx.beginPath()
    ctx.moveTo(0, this.surfaceY)
    ctx.lineTo(w, this.surfaceY)
    ctx.stroke()

    // Roots: live while growing, cached once fully grown.
    if (light > 0.995) {
      if (!this.cache) {
        const c = document.createElement('canvas')
        c.width = this.canvas.width
        c.height = this.canvas.height
        const cx = c.getContext('2d')
        cx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
        this.drawRoots(cx, 1, 1)
        this.cache = c
      }
      ctx.save()
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.drawImage(this.cache, 0, 0)
      ctx.restore()
    } else {
      this.drawRoots(ctx, light, light)
    }

    // Particles: drift, shimmer, part around the pointer.
    const px = this.pointer.x
    const py = this.pointer.y
    const dots = new Map()
    for (const p of this.particles) {
      if (dt) {
        p.phase += dt * 0.0012
        p.x += p.vx * dt * 0.06 + Math.sin(p.phase) * 0.05
        p.y += p.vy * dt * 0.06
        const dx = p.x - px
        const dy = p.y - py
        const d2 = dx * dx + dy * dy
        if (d2 < 9000) {
          const f = (1 - d2 / 9000) * 0.9
          p.x += (dx / Math.sqrt(d2 + 1)) * f
          p.y += (dy / Math.sqrt(d2 + 1)) * f
        }
        if (p.x < -4) p.x = w + 4
        if (p.x > w + 4) p.x = -4
        const top = p.soil ? this.surfaceY + 2 : this.surfaceY - h * 0.3
        const bottom = p.soil ? h : this.surfaceY
        if (p.y < top) p.y = bottom - 2
        if (p.y > bottom) p.y = top + 2
      }
      const twinkle = 0.65 + 0.35 * Math.sin(p.phase * 2.3)
      const a = (p.soil ? 0.12 + 0.68 * light : 0.05 + 0.3 * light) * twinkle
      const bucket = Math.min(4, Math.floor(a * 5))
      const key = (p.blue ? 5 : 0) + bucket
      let path = dots.get(key)
      if (!path) {
        path = new Path2D()
        dots.set(key, path)
      }
      path.moveTo(p.x + p.r, p.y)
      path.arc(p.x, p.y, p.r, 0, TAU)
    }
    for (const [key, path] of dots) {
      const alpha = ((key % 5) + 0.5) / 5
      ctx.fillStyle = key >= 5 ? `rgba(61, 123, 255, ${alpha})` : `rgba(233, 238, 248, ${alpha * 0.85})`
      ctx.fill(path)
    }
  }
}
