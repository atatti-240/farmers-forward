/* ==========================================================================
   Farmers Forward - DashboardProvider (BUILD_CONTRACT §5)
   React context store for the 4-step field workflow. State is persisted to
   localStorage under `ff_field_draft`: auto-saved on every change, loaded on
   mount, and cleared on a successful submit or an explicit reset.
   Live soil health scores for the map colours are fetched as measurements are
   entered and kept in memory only (they are derived from `data`).
   ========================================================================== */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'

import api from '../api/client.js'

const STORAGE_KEY = 'ff_field_draft'
// Results of the last submit, kept separately so a refresh on Step 4 re-hydrates
// the results view even though the editable draft is cleared on submit.
const RESULTS_KEY = 'ff_last_results'
const MEASUREMENTS = ['ph', 'ec', 'nitrogen', 'phosphorus', 'potassium']
// Wait for typing to pause before asking the server for new scores.
const SCORE_DEBOUNCE_MS = 450

const DashboardContext = createContext(null)

/** @returns {string} Today's date as `YYYY-MM-DD` (local time). */
function todayISO() {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

/** @returns {Object} A pristine draft matching the §5 store shape. */
function defaultState() {
  return {
    step: 1,
    farmName: '',
    date: todayISO(),
    polygon: [],
    gridMode: 'auto',
    rows: 3,
    cols: 3,
    points: [],
    tweakMode: false,
    // Last map view ({ center: [lat, lng], zoom }) so every step's map opens on
    // exactly the view the user left, instead of re-fitting and shifting.
    mapView: null,
    // Farm Finder search pins: { id, lat, lng, title, name }.
    finderPins: [],
    data: {},
    analysis: null,
    farmId: null,
  }
}

/** Load a persisted draft, falling back to last results, then a pristine one. */
function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') {
        // Coerce structural fields so a corrupt/hand-edited draft can never
        // crash the dashboard on load (e.g. points saved as a non-array).
        const merged = { ...defaultState(), ...parsed }
        merged.points = Array.isArray(merged.points) ? merged.points : []
        merged.polygon = Array.isArray(merged.polygon) ? merged.polygon : []
        merged.data =
          merged.data && typeof merged.data === 'object' && !Array.isArray(merged.data)
            ? merged.data
            : {}
        merged.mapView = isMapView(merged.mapView) ? merged.mapView : null
        merged.finderPins = Array.isArray(merged.finderPins)
          ? merged.finderPins.filter(isFinderPin)
          : []
        return merged
      }
    }
    // No editable draft: if we just submitted (results persisted), re-hydrate
    // Step 4 so a refresh doesn't lose the analysis.
    const rawResults = localStorage.getItem(RESULTS_KEY)
    if (rawResults) {
      const res = JSON.parse(rawResults)
      if (res && typeof res === 'object' && res.analysis) {
        return {
          ...defaultState(),
          step: 4,
          analysis: res.analysis,
          farmId: res.farmId,
          // Kept so the results map can redraw the boundary and grid cells.
          polygon: Array.isArray(res.polygon) ? res.polygon : [],
          points: Array.isArray(res.points) ? res.points : [],
        }
      }
    }
    return defaultState()
  } catch {
    return defaultState()
  }
}

/** @returns {boolean} Whether `v` is a usable `{ center: [lat, lng], zoom }`. */
function isMapView(v) {
  return (
    !!v &&
    Array.isArray(v.center) &&
    v.center.length === 2 &&
    v.center.every(Number.isFinite) &&
    Number.isFinite(v.zoom)
  )
}

/** @returns {boolean} Whether `p` is a well-formed Farm Finder pin. */
function isFinderPin(p) {
  return (
    !!p &&
    typeof p.id === 'string' &&
    typeof p.title === 'string' &&
    typeof p.name === 'string' &&
    Number.isFinite(p.lat) &&
    Math.abs(p.lat) <= 90 &&
    Number.isFinite(p.lng) &&
    Math.abs(p.lng) <= 180
  )
}

/** A completed sample has one crop name and five numeric measurements. */
function isSectionComplete(data, label) {
  const values = data[label]
  if (!values || typeof values.crop_type !== 'string' || !values.crop_type.trim() || values.crop_type.trim().length > 100) return false
  return MEASUREMENTS.every((field) => {
    const raw = values[field]
    if (raw === undefined || raw === null || String(raw).trim() === '') {
      return false
    }
    return Number.isFinite(Number(raw))
  })
}

/**
 * Stable key for a point's five measurements, or `null` until all five are
 * numeric. Points with identical readings share one cached score.
 */
function measurementKey(values) {
  if (!values) return null
  const nums = []
  for (const field of MEASUREMENTS) {
    const raw = values[field]
    if (raw === undefined || raw === null || String(raw).trim() === '') return null
    const n = Number(raw)
    if (!Number.isFinite(n)) return null
    nums.push(n)
  }
  return nums.join('|')
}

/** Next unused manual label in the `A1, A2, …` sequence. */
function nextManualLabel(points) {
  const used = new Set(points.map((p) => p.label))
  let i = 1
  while (used.has(`A${i}`)) i += 1
  return `A${i}`
}

/**
 * Provider for the dashboard store.
 * @param {{ children: React.ReactNode }} props
 */
export default function DashboardProvider({ children }) {
  const [state, setState] = useState(loadState)

  // Allows submit/reset to clear localStorage without the next auto-save
  // immediately re-writing the just-cleared draft.
  const skipSaves = useRef(0)

  // measurementKey -> ML health score, filled by /api/score as data comes in.
  const [scoreCache, setScoreCache] = useState({})
  const pendingScores = useRef(new Set())

  useEffect(() => {
    if (skipSaves.current > 0) {
      skipSaves.current -= 1
      return
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    } catch {
      /* ignore storage quota / private-mode errors */
    }
  }, [state])

  const patch = useCallback((updater) => {
    setState((prev) => {
      const next = typeof updater === 'function' ? updater(prev) : updater
      return { ...prev, ...next }
    })
  }, [])

  // Score every point whose five measurements are complete and not yet cached.
  // Each point is coloured as soon as its own data is in; failures just leave
  // the point uncoloured until its values change again.
  useEffect(() => {
    const wanted = new Set()
    for (const p of state.points) {
      const key = measurementKey(state.data[p.label])
      if (key && !(key in scoreCache) && !pendingScores.current.has(key)) {
        wanted.add(key)
      }
    }
    if (wanted.size === 0) return undefined

    const timer = setTimeout(async () => {
      const keys = [...wanted]
      keys.forEach((k) => pendingScores.current.add(k))
      try {
        const res = await api.scoreSections(
          keys.map((key) => {
            const [ph, ec, nitrogen, phosphorus, potassium] = key.split('|').map(Number)
            return { section_label: key, ph, ec, nitrogen, phosphorus, potassium }
          })
        )
        const found = {}
        for (const s of (res && res.scores) || []) {
          if (Number.isFinite(s.health_score)) found[s.section_label] = s.health_score
        }
        setScoreCache((prev) => ({ ...prev, ...found }))
      } catch {
        /* offline / signed out: leave these points in the default colour */
      } finally {
        keys.forEach((k) => pendingScores.current.delete(k))
      }
    }, SCORE_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [state.points, state.data, scoreCache])

  // label -> score for points that currently have one.
  const scores = {}
  for (const p of state.points) {
    const key = measurementKey(state.data[p.label])
    if (key && key in scoreCache) scores[p.label] = scoreCache[key]
  }

  /* -------------------------------------------------------- simple setters */
  const setFarmName = useCallback((farmName) => patch({ farmName }), [patch])
  const setDate = useCallback((date) => patch({ date }), [patch])
  const setPolygon = useCallback((polygon) => patch({ polygon: polygon || [] }), [patch])
  const setGridMode = useCallback((gridMode) => patch({ gridMode }), [patch])
  const setRows = useCallback((rows) => patch({ rows }), [patch])
  const setCols = useCallback((cols) => patch({ cols }), [patch])
  const setTweakMode = useCallback((tweakMode) => patch({ tweakMode }), [patch])
  const goStep = useCallback((step) => patch({ step }), [patch])
  const setMapView = useCallback((mapView) => patch({ mapView }), [patch])

  /* -------------------------------------------------------- point helpers */

  // Replace the whole point set, pruning measurement data for labels that were
  // removed or now sit somewhere else (a regenerated grid reuses A1, A2, …).
  const setPoints = useCallback(
    (points) => {
      const list = Array.isArray(points) ? points : []
      patch((prev) => {
        const before = new Map(prev.points.map((p) => [p.label, p]))
        const data = {}
        for (const p of list) {
          const old = before.get(p.label)
          if (old && old.lat === p.lat && old.lng === p.lng && prev.data[p.label]) {
            data[p.label] = prev.data[p.label]
          }
        }
        return { points: list, data }
      })
    },
    [patch]
  )

  // Append a manual point at the next `A#` label.
  const addPoint = useCallback(
    (lat, lng) => {
      patch((prev) => ({
        points: [
          ...prev.points,
          { label: nextManualLabel(prev.points), lat, lng },
        ],
      }))
    },
    [patch]
  )

  // Move an existing point (auto tweak / manual drag).
  const updatePoint = useCallback(
    (label, lat, lng) => {
      patch((prev) => ({
        points: prev.points.map((p) =>
          p.label === label ? { ...p, lat, lng } : p
        ),
      }))
    },
    [patch]
  )

  // Remove a point and its measurement data.
  const removePoint = useCallback(
    (label) => {
      patch((prev) => {
        const data = { ...prev.data }
        delete data[label]
        return { points: prev.points.filter((p) => p.label !== label), data }
      })
    },
    [patch]
  )

  const clearPoints = useCallback(() => setPoints([]), [setPoints])

  /* --------------------------------------------------- farm finder pins */

  // Add a search pin; returns the pin (an existing one at the same spot is reused).
  const addFinderPin = useCallback(
    (pin) => {
      const existing = state.finderPins.find(
        (p) => p.lat.toFixed(6) === pin.lat.toFixed(6) && p.lng.toFixed(6) === pin.lng.toFixed(6)
      )
      if (existing) return existing
      const created = {
        ...pin,
        id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      }
      patch((prev) => ({ finderPins: [...prev.finderPins, created] }))
      return created
    },
    [patch, state.finderPins]
  )

  const removeFinderPin = useCallback(
    (id) => patch((prev) => ({ finderPins: prev.finderPins.filter((p) => p.id !== id) })),
    [patch]
  )

  const clearFinderPins = useCallback(() => patch({ finderPins: [] }), [patch])

  /* --------------------------------------------------- measurement helpers */

  const setSectionData = useCallback(
    (label, field, value) => {
      patch((prev) => ({
        data: {
          ...prev.data,
          [label]: { ...(prev.data[label] || {}), [field]: value },
        },
      }))
    },
    [patch]
  )

  const sectionComplete = useCallback(
    (label) => isSectionComplete(state.data, label),
    [state.data]
  )

  const completeCount = state.points.reduce(
    (n, p) => (isSectionComplete(state.data, p.label) ? n + 1 : n),
    0
  )
  const allComplete =
    state.points.length > 0 && completeCount === state.points.length

  /* -------------------------------------------------- submit / reset flows */

  // Store the analysis result, clear the editable draft, advance to step 4, and
  // persist the results separately so a refresh on Step 4 keeps them.
  const submitSuccess = useCallback((analysis, farmId) => {
    try {
      localStorage.removeItem(STORAGE_KEY)
      localStorage.setItem(
        RESULTS_KEY,
        JSON.stringify({ analysis, farmId, polygon: state.polygon, points: state.points })
      )
    } catch {
      /* ignore */
    }
    skipSaves.current += 1
    setState((prev) => ({ ...prev, analysis, farmId, step: 4 }))
  }, [state.polygon, state.points])

  // Wipe everything back to a pristine step-1 draft.
  const resetDraft = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY)
      localStorage.removeItem(RESULTS_KEY)
    } catch {
      /* ignore */
    }
    skipSaves.current += 1
    setState(defaultState())
  }, [])

  const value = {
    // raw state
    step: state.step,
    farmName: state.farmName,
    date: state.date,
    polygon: state.polygon,
    gridMode: state.gridMode,
    rows: state.rows,
    cols: state.cols,
    points: state.points,
    tweakMode: state.tweakMode,
    mapView: state.mapView,
    finderPins: state.finderPins,
    data: state.data,
    analysis: state.analysis,
    farmId: state.farmId,
    // derived
    completeCount,
    scores,
    total: state.points.length,
    allComplete,
    // setters
    setFarmName,
    setDate,
    setPolygon,
    setGridMode,
    setRows,
    setCols,
    setTweakMode,
    setMapView,
    setPoints,
    addPoint,
    updatePoint,
    removePoint,
    clearPoints,
    addFinderPin,
    removeFinderPin,
    clearFinderPins,
    setSectionData,
    // helpers
    sectionComplete,
    goStep,
    submitSuccess,
    resetDraft,
  }

  return (
    <DashboardContext.Provider value={value}>
      {children}
    </DashboardContext.Provider>
  )
}

/**
 * Access the dashboard store.
 * @returns {Object} The store value provided by {@link DashboardProvider}.
 */
export function useDashboard() {
  const ctx = useContext(DashboardContext)
  if (!ctx) {
    throw new Error('useDashboard must be used within a DashboardProvider')
  }
  return ctx
}
