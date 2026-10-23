/* ==========================================================================
   Farmers Forward (public site) - Farm Finder
   Search box above the field map: type coordinates or an address (looked up
   with OpenStreetMap Nominatim), drop a pin there, and fly the map to it.
   Pins accumulate until removed from their popup or the list under the box.
   ========================================================================== */

import { useEffect, useId, useRef, useState } from 'react'
import { Marker, Popup, Tooltip } from 'react-leaflet'
import L from 'leaflet'

import { useTool } from './ToolProvider.jsx'
import { parseCoordinates } from './geo.js'
import { geocodeAddress } from './geocode.js'

const PIN_ZOOM = 17
const FLY_SECONDS = 1.2

const PIN_ICON = L.divIcon({
  className: 'finder-pin',
  html:
    '<svg viewBox="0 0 26 36" width="26" height="36" aria-hidden="true">' +
    '<path class="finder-pin__body" d="M13 1.5C6.6 1.5 1.5 6.6 1.5 12.9c0 8.7 11.5 21.6 11.5 21.6s11.5-12.9 11.5-21.6C24.5 6.6 19.4 1.5 13 1.5z"/>' +
    '<circle class="finder-pin__hole" cx="13" cy="13" r="4.5"/></svg>',
  iconSize: [26, 36],
  iconAnchor: [13, 35],
  popupAnchor: [0, -32],
  tooltipAnchor: [11, -22],
})

/** Short label for a Nominatim result: "4800 Main Street" rather than "4800". */
function shortTitle(displayName) {
  const parts = displayName.split(',').map((s) => s.trim()).filter(Boolean)
  let title = parts[0] || displayName
  if (/^[\d-]+[a-z]?$/i.test(title) && parts[1]) title = `${title} ${parts[1]}`
  return title.length > 40 ? `${title.slice(0, 39)}…` : title
}

/** Move the map to a pin (or a search result's extent) with a smooth flight. */
function flyTo(map, { lat, lng }, bounds = null) {
  if (!map) return
  const animate = !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (bounds) {
    if (animate) map.flyToBounds(bounds, { maxZoom: PIN_ZOOM, duration: FLY_SECONDS })
    else map.fitBounds(bounds, { maxZoom: PIN_ZOOM, animate: false })
    return
  }
  const zoom = Math.max(map.getZoom(), PIN_ZOOM)
  if (animate) map.flyTo([lat, lng], zoom, { duration: FLY_SECONDS })
  else map.setView([lat, lng], zoom, { animate: false })
}

/** Search form plus the list of dropped pins. Renders outside the map. */
export default function FarmFinder({ map }) {
  const { finderPins, addFinderPin, removeFinderPin, clearFinderPins } = useTool()
  const id = useId()
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const request = useRef(null)

  useEffect(() => () => request.current?.abort(), [])

  async function handleSubmit(event) {
    event.preventDefault()
    const text = query.trim()
    if (!text || busy) return
    setError('')
    setStatus('')

    let found
    let bounds = null
    const coords = parseCoordinates(text)
    if (coords?.error) {
      setError(coords.error)
      return
    }
    if (coords) {
      found = {
        lat: coords.lat,
        lng: coords.lng,
        title: `${coords.lat.toFixed(4)}, ${coords.lng.toFixed(4)}`,
        name: `${coords.lat.toFixed(6)}, ${coords.lng.toFixed(6)}`,
      }
    } else {
      request.current = new AbortController()
      setBusy(true)
      setStatus('Looking up address…')
      try {
        const hit = await geocodeAddress(text, { signal: request.current.signal })
        if (!hit) {
          setStatus('')
          setError(`No match for “${text}”. Add the city and state, or enter coordinates.`)
          return
        }
        found = { lat: hit.lat, lng: hit.lng, title: shortTitle(hit.name), name: hit.name }
        bounds = hit.bounds
      } catch (err) {
        if (err?.name === 'AbortError') return
        setStatus('')
        setError(
          err instanceof TypeError
            ? 'Couldn’t reach the address service. Check your connection, or enter coordinates.'
            : err.message
        )
        return
      } finally {
        setBusy(false)
      }
    }

    const pin = addFinderPin(found)
    const reused = finderPins.includes(pin)
    flyTo(map, pin, reused ? null : bounds)
    setQuery('')
    setStatus(reused ? `Already pinned: ${pin.title}` : `Pinned ${pin.title}`)
  }

  const errorId = `${id}-error`
  const helpId = `${id}-help`

  return (
    <div className="finder">
      <form className="finder__form" role="search" onSubmit={handleSubmit}>
        <label className="label" htmlFor={id}>
          Find a farm
        </label>
        <div className="finder__row">
          <input
            id={id}
            className="input finder__input"
            type="text"
            value={query}
            placeholder="Address or 29.5310, -95.7730"
            autoComplete="off"
            spellCheck={false}
            enterKeyHint="search"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${errorId} ${helpId}` : helpId}
            onChange={(e) => {
              setQuery(e.target.value)
              if (error) setError('')
            }}
          />
          <button
            type="submit"
            className="btn btn--secondary"
            disabled={busy || !query.trim()}
          >
            {busy ? 'Finding…' : 'Find'}
          </button>
        </div>
        {error && (
          <p id={errorId} className="error-text finder__msg" role="alert">
            {error}
          </p>
        )}
        <p className="finder__status" role="status">
          {error ? '' : status}
        </p>
        <p id={helpId} className="help finder__msg">
          Each search adds a pin. Addresses are looked up with{' '}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
            OpenStreetMap
          </a>
          .
        </p>
      </form>

      {finderPins.length > 0 && (
        <div className="finder__pins">
          <ul className="finder__list" aria-label="Farm Finder pins">
            {finderPins.map((pin) => (
              <li key={pin.id} className="finder__chip">
                <button
                  type="button"
                  className="finder__chip-go"
                  title={`Show ${pin.name} on the map`}
                  onClick={() => flyTo(map, pin)}
                >
                  <span className="finder__chip-dot" aria-hidden="true" />
                  {pin.title}
                </button>
                <button
                  type="button"
                  className="finder__chip-remove"
                  aria-label={`Remove pin ${pin.title}`}
                  title="Remove pin"
                  onClick={() => removeFinderPin(pin.id)}
                >
                  &times;
                </button>
              </li>
            ))}
          </ul>
          {finderPins.length > 1 && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={clearFinderPins}
            >
              Clear all pins
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/** The Farm Finder pins, rendered inside a MapContainer. */
export function FarmFinderPins() {
  const { finderPins, removeFinderPin } = useTool()
  return finderPins.map((pin) => (
    <Marker
      key={pin.id}
      position={[pin.lat, pin.lng]}
      icon={PIN_ICON}
      title={pin.name}
      // Keep sample points (also markers) clickable on top of search pins.
      zIndexOffset={-1000}
    >
      <Tooltip permanent direction="right" className="label-tip finder-tip">
        {pin.title}
      </Tooltip>
      <Popup className="finder-popup">
        <div className="finder-popup__title">{pin.title}</div>
        {pin.name !== pin.title && <p className="finder-popup__name">{pin.name}</p>}
        <p className="finder-popup__coords">
          {pin.lat.toFixed(6)}, {pin.lng.toFixed(6)}
        </p>
        <button
          type="button"
          className="btn btn--ghost btn--sm finder-popup__remove"
          onClick={(event) => {
            // React handles this at the popup and removes the popup at once, so
            // by the time the click reaches the map Leaflet can no longer tell it
            // came from a popup and would drop a boundary corner. Stop it here.
            event.stopPropagation()
            removeFinderPin(pin.id)
          }}
        >
          &times; Remove pin
        </button>
      </Popup>
    </Marker>
  ))
}
