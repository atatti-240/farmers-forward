/* Farmers Forward wordmark: a sprout-in-a-circle mark and the name. */

export default function Wordmark({ small = false }) {
  return (
    <span className={`wordmark${small ? ' wordmark--small' : ''}`}>
      <svg className="wordmark__mark" viewBox="0 0 32 32" aria-hidden="true">
        <circle cx="16" cy="16" r="15" />
        <path d="M16 25V14" />
        <path d="M16 18c-4.5 0-7-2.5-7.5-7 4.5 0 7 2.5 7.5 7z" />
        <path d="M16 15.5c.5-4.5 3-7 7.5-7-.5 4.5-3 7-7.5 7z" />
      </svg>
      <span className="wordmark__name">Farmers Forward</span>
    </span>
  )
}
