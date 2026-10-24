/* A thin arrow for buttons and links; slides on hover via .btn__arrow. */

export default function Arrow({ back = false, down = false }) {
  const rotate = back ? 180 : down ? 90 : 0
  return (
    <svg
      className="btn__arrow"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={rotate ? { rotate: `${rotate}deg` } : undefined}
    >
      <path d="M2.5 8h11M9 3.5 13.5 8 9 12.5" />
    </svg>
  )
}
