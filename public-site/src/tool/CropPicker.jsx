import { useEffect, useId, useRef, useState } from 'react'

const COMMON_CROPS = [
  'Corn', 'Cotton', 'Soybeans', 'Sorghum', 'Wheat', 'Rice', 'Sugarcane',
  'Hay', 'Bermudagrass', 'Peanuts', 'Sweet potatoes', 'Alfalfa', 'Oats',
  'Barley', 'Rye', 'Sunflowers', 'Canola', 'Tomatoes', 'Peppers',
  'Blueberries', 'Strawberries', 'Hemp', 'Potatoes', 'Watermelons',
  'Cucumbers', 'Squash', 'Okra', 'Pecans',
]

/** One editable crop value: suggestions help, but a custom name is valid too. */
export default function CropPicker({ value = '', onChange }) {
  const id = useId()
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const query = value.trim()
  const matches = COMMON_CROPS.filter((crop) =>
    crop.toLowerCase().includes(query.toLowerCase())
  )
  const exact = COMMON_CROPS.find((crop) => crop.toLowerCase() === query.toLowerCase())
  const options = matches.map((crop) => ({ value: crop, label: crop }))
  if (query && !exact) options.push({ value: query, label: `Use “${query}”` })

  useEffect(() => {
    if (open && active >= 0) {
      listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' })
    }
  }, [open, active])

  function choose(crop) {
    onChange(crop)
    setOpen(false)
    setActive(-1)
  }

  function handleKeyDown(event) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      const direction = event.key === 'ArrowDown' ? 1 : -1
      setActive((index) => !open || index < 0
        ? (direction === 1 ? 0 : options.length - 1)
        : (index + direction + options.length) % options.length)
    } else if (event.key === 'Enter' && open) {
      event.preventDefault()
      if (active >= 0 && options[active]) choose(options[active].value)
      else choose(exact || query)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      setOpen(false)
      setActive(-1)
    }
  }

  return (
    <div className="field crop-field">
      <label className="label" htmlFor={id}>Crop <span className="label__opt">(required)</span></label>
      <div className="crop-picker">
        <input
          ref={inputRef}
          id={id}
          className="input crop-picker__input"
          type="text"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-activedescendant={open && active >= 0 ? `${id}-option-${active}` : undefined}
          aria-describedby={`${id}-help`}
          autoComplete="off"
          maxLength={100}
          required
          placeholder="Search crops or type your own"
          value={value}
          onChange={(event) => {
            onChange(event.target.value)
            setActive(-1)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onBlur={() => choose(exact || query)}
          onKeyDown={handleKeyDown}
        />
        <svg className="crop-picker__arrow" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
        {open && (
          <ul ref={listRef} id={`${id}-list`} className="crop-picker__list" role="listbox" aria-label="Crop suggestions">
            {options.map((option, index) => (
              <li
                id={`${id}-option-${index}`}
                key={option.value}
                role="option"
                aria-selected={active === index}
                className={`crop-picker__option${active === index ? ' crop-picker__option--active' : ''}`}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => { choose(option.value); inputRef.current?.focus() }}
              >
                {option.label}
              </li>
            ))}
          </ul>
        )}
      </div>
      <span id={`${id}-help`} className="help">One crop for this sample point. Custom names are welcome.</span>
    </div>
  )
}
