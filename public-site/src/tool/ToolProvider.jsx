/* ==========================================================================
   Farmers Forward (public site) - ToolProvider
   React context store for the 4-step assessment. Adapted from the admin
   dashboard's store, with two differences:
     - Only the editable draft (field, points, readings) is kept in
       localStorage, so a refresh mid-entry loses nothing. Results are kept in
       memory only: they live in the POST /assessments response and nowhere
       else, and there is no way to load them back.
     - Live scores are debounced (SCORE_DEBOUNCE_MS) so typing never trips
       the public API's rate limit.
   ========================================================================== */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'

import { scoreSections } from '../api/client.js'
import { mergeGridPoints, nextLabel } from './points.js'

const STORAGE_KEY = 'ffp_draft_v1'
export const MEASUREMENTS = ['ph', 'ec', 'nitrogen', 'phosphorus', 'potassium']
// The public API's ceiling per request.
export const MAX_POINTS = 150
const SCORE_DEBOUNCE_MS = 400

// Inclusive ranges the API accepts (backend/public_api.py MEASUREMENT_RANGES).
export const RANGES = {
  ph: [0, 14],
  ec: [0, 30],
  nitrogen: [0, 2000],
  phosphorus: [0, 2000],
  potassium: [0, 10000],
}

const ToolContext = createContext(null)

function todayISO() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function defaultState() {
  return {
    step: 1,
    farmName: '',
    date: todayISO(),
    polygon: [],
    rows: 3,
    cols: 3,
    grid: null,
    baselinePoints: true,
    points: [],
    tweakMode: false,
    mapView: null,
    finderPins: [],
    data: {},
  }
}

const isNum = Number.isFinite

function isMapView(v) {
  return !!v && Array.isArray(v.center) && v.center.length === 2 &&
    v.center.every(isNum) && isNum(v.zoom)
}

function isGrid(g) {
  return !!g && Number.isInteger(g.rows) && Number.isInteger(g.cols) && g.rows >= 1 && g.cols >= 1
}

function isPoint(p) {
  return !!p && typeof p.label === 'string' && isNum(p.lat) && isNum(p.lng)
}

function isFinderPin(p) {
  return !!p && typeof p.id === 'string' && typeof p.title === 'string' &&
    typeof p.name === 'string' && isNum(p.lat) && Math.abs(p.lat) <= 90 &&
    isNum(p.lng) && Math.abs(p.lng) <= 180
}

/** The saved draft, coerced so a corrupt one can never crash the tool. */
function loadState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (!parsed || typeof parsed !== 'object') return defaultState()
    const s = { ...defaultState(), ...parsed }
    s.points = Array.isArray(s.points) ? s.points.filter(isPoint).slice(0, MAX_POINTS) : []
    s.polygon = Array.isArray(s.polygon)
      ? s.polygon.filter((v) => Array.isArray(v) && v.length === 2 && v.every(isNum))
      : []
    s.grid = isGrid(s.grid) ? s.grid : null
    s.baselinePoints = s.baselinePoints !== false
    s.data = s.data && typeof s.data === 'object' && !Array.isArray(s.data) ? s.data : {}
    s.mapView = isMapView(s.mapView) ? s.mapView : null
    s.finderPins = Array.isArray(s.finderPins) ? s.finderPins.filter(isFinderPin) : []
    // Results are never saved, so a reload always lands on an editable step.
    // A reload on the results step comes back to "Check & run", one click
    // from the same results.
    s.step = s.step === 4 ? 3 : [1, 2, 3].includes(s.step) ? s.step : 1
    if (s.step > 1 && s.points.length === 0) s.step = 1
    return s
  } catch {
    return defaultState()
  }
}

/** A reading's problem in plain words, or '' when it is fine (or empty). */
export function readingError(field, raw) {
  if (raw === undefined || raw === null || String(raw).trim() === '') return ''
  const n = Number(raw)
  if (!isNum(n)) return 'Enter a number.'
  const [lo, hi] = RANGES[field]
  if (n < lo || n > hi) return `Must be between ${lo} and ${hi.toLocaleString()}.`
  return ''
}

function isSectionComplete(data, label) {
  const v = data[label]
  if (!v || typeof v.crop_type !== 'string' || !v.crop_type.trim() || v.crop_type.trim().length > 100) {
    return false
  }
  return MEASUREMENTS.every((f) => {
    const raw = v[f]
    return raw !== undefined && raw !== null && String(raw).trim() !== '' &&
      isNum(Number(raw)) && !readingError(f, raw)
  })
}

/** Cache key for a point's readings + crop, or null until all five are valid. */
function scoreKey(values) {
  if (!values) return null
  const nums = []
  for (const f of MEASUREMENTS) {
    const raw = values[f]
    if (raw === undefined || raw === null || String(raw).trim() === '') return null
    const n = Number(raw)
    if (!isNum(n) || readingError(f, raw)) return null
    nums.push(n)
  }
  const crop = typeof values.crop_type === 'string'
    ? values.crop_type.trim().replace(/\s+/g, ' ').toLowerCase().slice(0, 100)
    : ''
  return JSON.stringify([...nums, crop])
}

export default function ToolProvider({ children }) {
  const [state, setState] = useState(loadState)
  // In memory only: { analysis, pdfToken, pdfExpiresAt, polygon, points }.
  const [result, setResult] = useState(null)
  const [scoreCache, setScoreCache] = useState({})
  const pendingScores = useRef(new Set())

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    } catch {
      /* private mode / quota: the draft just isn't kept */
    }
  }, [state])

  const patch = useCallback((updater) => {
    setState((prev) => ({ ...prev, ...(typeof updater === 'function' ? updater(prev) : updater) }))
  }, [])

  // Score points whose readings are complete and not cached yet, once typing
  // has paused. A failure leaves those points uncoloured until they change.
  useEffect(() => {
    const wanted = new Set()
    for (const p of state.points) {
      const key = scoreKey(state.data[p.label])
      if (key && !(key in scoreCache) && !pendingScores.current.has(key)) wanted.add(key)
    }
    if (wanted.size === 0) return undefined
    const timer = setTimeout(async () => {
      const keys = [...wanted].slice(0, MAX_POINTS)
      keys.forEach((k) => pendingScores.current.add(k))
      try {
        // Labels are the key's index: the API caps labels at 40 characters.
        const res = await scoreSections(keys.map((key, i) => {
          const [ph, ec, nitrogen, phosphorus, potassium, crop] = JSON.parse(key)
          const section = { section_label: `k${i}`, ph, ec, nitrogen, phosphorus, potassium }
          if (crop) section.crop_type = crop
          return section
        }))
        const found = {}
        for (const s of (res && res.scores) || []) {
          const i = Number(String(s.section_label).slice(1))
          if (isNum(s.health_score) && keys[i] !== undefined) found[keys[i]] = s.health_score
        }
        setScoreCache((prev) => ({ ...prev, ...found }))
      } catch {
        /* offline or rate limited: points stay uncoloured for now */
      } finally {
        keys.forEach((k) => pendingScores.current.delete(k))
      }
    }, SCORE_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [state.points, state.data, scoreCache])

  const scores = {}
  for (const p of state.points) {
    const key = scoreKey(state.data[p.label])
    if (key && key in scoreCache) scores[p.label] = scoreCache[key]
  }

  const set = (key) => (value) => patch({ [key]: value })
  /* eslint-disable react-hooks/exhaustive-deps */
  const setFarmName = useCallback(set('farmName'), [patch])
  const setDate = useCallback(set('date'), [patch])
  const setGrid = useCallback(set('grid'), [patch])
  const setBaselinePoints = useCallback(set('baselinePoints'), [patch])
  const setRows = useCallback(set('rows'), [patch])
  const setCols = useCallback(set('cols'), [patch])
  const setTweakMode = useCallback(set('tweakMode'), [patch])
  const setMapView = useCallback(set('mapView'), [patch])
  /* eslint-enable react-hooks/exhaustive-deps */
  const setPolygon = useCallback((polygon) => patch({ polygon: polygon || [] }), [patch])

  const goStep = useCallback((step) => {
    patch({ step })
    window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' })
  }, [patch])

  const setGridPoints = useCallback((gridPoints) => {
    const list = Array.isArray(gridPoints) ? gridPoints : []
    patch((prev) => {
      const merged = mergeGridPoints(prev.points, prev.data, list)
      return { ...merged, points: merged.points.slice(0, MAX_POINTS) }
    })
  }, [patch])

  const addPoint = useCallback((lat, lng) => {
    patch((prev) => prev.points.length >= MAX_POINTS
      ? {}
      : { points: [...prev.points, { label: nextLabel(prev.points), lat, lng }] })
  }, [patch])

  const updatePoint = useCallback((label, lat, lng) => {
    patch((prev) => ({ points: prev.points.map((p) => (p.label === label ? { ...p, lat, lng } : p)) }))
  }, [patch])

  const removePoint = useCallback((label) => {
    patch((prev) => {
      const data = { ...prev.data }
      delete data[label]
      return { points: prev.points.filter((p) => p.label !== label), data }
    })
  }, [patch])

  const clearPoints = useCallback(() => patch({ points: [], data: {} }), [patch])

  const addFinderPin = useCallback((pin) => {
    const existing = state.finderPins.find(
      (p) => p.lat.toFixed(6) === pin.lat.toFixed(6) && p.lng.toFixed(6) === pin.lng.toFixed(6))
    if (existing) return existing
    const created = { ...pin, id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}` }
    patch((prev) => ({ finderPins: [...prev.finderPins, created] }))
    return created
  }, [patch, state.finderPins])

  const removeFinderPin = useCallback(
    (id) => patch((prev) => ({ finderPins: prev.finderPins.filter((p) => p.id !== id) })), [patch])
  const clearFinderPins = useCallback(() => patch({ finderPins: [] }), [patch])

  const setSectionData = useCallback((label, field, value) => {
    patch((prev) => ({
      data: { ...prev.data, [label]: { ...(prev.data[label] || {}), [field]: value } },
    }))
  }, [patch])

  /** Replace the readings of many points at once (example data). */
  const fillData = useCallback((byLabel) => {
    patch((prev) => {
      const data = { ...prev.data }
      for (const [label, values] of Object.entries(byLabel)) {
        data[label] = { ...(data[label] || {}), ...values }
      }
      return { data }
    })
  }, [patch])

  /** Load a whole demo field (boundary, grid, points) in one go. */
  const loadDemoField = useCallback((demo) => {
    patch({ ...demo, tweakMode: false, data: {}, mapView: null })
  }, [patch])

  const sectionComplete = useCallback((label) => isSectionComplete(state.data, label), [state.data])
  const completeCount = state.points.reduce((n, p) => (isSectionComplete(state.data, p.label) ? n + 1 : n), 0)
  const allComplete = state.points.length > 0 && completeCount === state.points.length

  const showResult = useCallback((response) => {
    setResult({
      analysis: response.analysis,
      pdfToken: response.pdf_token,
      pdfExpiresAt: response.pdf_token_expires_at,
      polygon: state.polygon,
      points: state.points,
    })
    patch({ step: 4 })
    window.scrollTo({ top: 0 })
  }, [patch, state.polygon, state.points])

  const resetAll = useCallback(() => {
    setResult(null)
    setState(defaultState())
    window.scrollTo({ top: 0 })
  }, [])

  /** Back from results to edit the same field (the readings are still there). */
  const editAgain = useCallback(() => {
    setResult(null)
    patch({ step: 2 })
  }, [patch])

  const value = {
    ...state,
    step: state.step === 4 && !result ? 3 : state.step,
    result,
    scores,
    completeCount,
    total: state.points.length,
    allComplete,
    setFarmName, setDate, setPolygon, setGrid, setBaselinePoints, setRows, setCols,
    setTweakMode, setMapView, setGridPoints, addPoint, updatePoint, removePoint,
    clearPoints, addFinderPin, removeFinderPin, clearFinderPins, setSectionData,
    fillData, loadDemoField, sectionComplete, goStep, showResult, resetAll, editAgain,
  }

  return <ToolContext.Provider value={value}>{children}</ToolContext.Provider>
}

export function useTool() {
  const ctx = useContext(ToolContext)
  if (!ctx) throw new Error('useTool must be used within a ToolProvider')
  return ctx
}
