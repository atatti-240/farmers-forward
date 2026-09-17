/**
 * Brand spinner (BUILD_CONTRACT §3.4 `.ff-spinner`).
 *
 * @param {Object} props
 * @param {'sm'|'md'|'lg'} [props.size='md'] - visual size.
 * @param {boolean} [props.center=false] - center within a min-height container.
 * @param {string} [props.label='Loading'] - accessible label.
 */
export default function Spinner({ size = 'md', center = false, label = 'Loading' }) {
  const sizeClass =
    size === 'sm' ? ' ff-spinner--sm' : size === 'lg' ? ' ff-spinner--lg' : ''

  const spinner = (
    <span
      className={`ff-spinner${sizeClass}`}
      role="status"
      aria-label={label}
    />
  )

  if (center) {
    return <div className="ff-spinner-center">{spinner}</div>
  }
  return spinner
}
