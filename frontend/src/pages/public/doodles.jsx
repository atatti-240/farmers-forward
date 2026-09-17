// Hand-drawn style SVG doodles for the public page. Drawn as single-stroke
// paths with round caps and deliberately uneven curves so they read as
// pen-and-ink rather than icon-font.

function Doodle({ children, className = '', size = 44, viewBox = '0 0 48 48', ...rest }) {
  return (
    <svg
      className={`ff-doodle ${className}`}
      width={size}
      height={size}
      viewBox={viewBox}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

export function SproutDoodle(props) {
  return (
    <Doodle {...props}>
      <path d="M24.3 40.5 C24 34.5 23.8 29 24.1 24.4" />
      <path d="M24.1 25.6 C18.4 25.9 14.2 22.1 13 15.8 C19.3 15.6 23.3 19.2 24.1 24.4" />
      <path d="M24.1 24.4 C24.6 18.6 28.8 14.3 35.2 13.2 C34.9 19.8 30.8 24.4 24.5 25.4" />
      <path d="M13.5 41.2 C17.8 40.3 29.6 40.9 34.6 40.6" />
    </Doodle>
  )
}

export function TrowelDoodle(props) {
  return (
    <Doodle {...props}>
      <path d="M28.5 17 C33.8 22.6 33 31.6 25.2 36.6 C20.4 30.8 20.6 22.4 27.6 16.6 Z" />
      <path d="M29.5 15.7 L37.6 7.6" />
      <path d="M36 7.2 C37.3 6.8 38.7 7.7 38.5 9.2" />
      <path d="M13.5 40.8 C16.2 39.9 19.4 40.4 21.4 40.9" />
      <path d="M11.2 36.9 C12.1 36.6 13 36.8 13.6 37.1" />
      <path d="M16.5 33.4 C17.2 33.2 17.8 33.3 18.3 33.5" />
    </Doodle>
  )
}

export function FlaskDoodle(props) {
  return (
    <Doodle {...props}>
      <path d="M19.4 8.4 L19.2 19.8 L12.1 35.8 C10.9 38.7 12.8 41 15.9 41 L32.2 41 C35.3 41 37.1 38.6 35.9 35.7 L28.8 19.9 L28.7 8.5" />
      <path d="M16.8 8.2 C21 7.8 27.2 8 31.2 8.3" />
      <path d="M15.4 31.6 C19.6 29.5 27.6 33.8 32.7 31.2" />
      <circle cx="22.2" cy="35.8" r="1.2" />
      <circle cx="27.4" cy="26.6" r="0.9" />
    </Doodle>
  )
}

export function ClipboardDoodle(props) {
  return (
    <Doodle {...props}>
      <path d="M15.2 10.6 C14 10.7 13.2 11.4 13.2 12.7 L13.4 39.5 C13.4 40.7 14.2 41.4 15.4 41.4 L32.8 41.2 C34 41.2 34.8 40.4 34.8 39.2 L34.6 12.5 C34.6 11.3 33.8 10.6 32.6 10.6 L29.4 10.6" />
      <path d="M18.6 10.7 C18.5 7.9 21 6.8 24 6.8 C27 6.8 29.4 8 29.3 10.8 C26 11.1 21.7 11 18.6 10.7 Z" />
      <path d="M18.4 19.6 C21.6 19.2 26.5 19.7 29.8 19.4" />
      <path d="M18.5 24.8 C21 24.5 25 24.9 27.4 24.7" />
      <path d="M18.2 34.2 L21.2 37 L28.6 29.2" />
    </Doodle>
  )
}

export function SunDoodle(props) {
  return (
    <Doodle viewBox="0 0 64 64" {...props}>
      <path d="M32 22.5 C37.4 22.2 41.6 26.6 41.4 31.8 C41.2 37 37 41.2 31.8 41.1 C26.7 41 22.7 36.8 22.8 31.7 C22.9 26.8 26.9 22.8 32 22.5 Z" />
      <path d="M32 9.5 L32.2 15.3" />
      <path d="M47.8 16.4 L44 20.4" />
      <path d="M54.5 32.2 L48.6 32" />
      <path d="M47.6 47.9 L43.8 43.9" />
      <path d="M31.7 54.5 L31.8 48.7" />
      <path d="M16.2 47.5 L20.1 43.7" />
      <path d="M9.5 31.8 L15.3 31.9" />
      <path d="M16.6 16.2 L20.4 20.1" />
    </Doodle>
  )
}

export function ArrowDoodle(props) {
  return (
    <Doodle viewBox="0 0 56 32" {...props}>
      <path d="M52 26 C42 12 24 6 6 12" />
      <path d="M12 7.5 L5.6 12.1 L12.4 16.8" />
    </Doodle>
  )
}

// Long hand-wavering underline used beneath section headings.
export function SquiggleUnderline({ className = '' }) {
  return (
    <svg
      className={`ff-squiggle ${className}`}
      viewBox="0 0 220 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path d="M3 8.5 C22 3.5 41 10.5 62 6.5 C83 2.5 101 10 122 6 C143 2 161 10.5 182 6.5 C196 4 208 6 217 7.5" />
    </svg>
  )
}
