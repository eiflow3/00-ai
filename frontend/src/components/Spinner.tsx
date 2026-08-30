/** A small activity indicator, sized to sit inline with text. */
export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`inline-block size-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600 ${className}`}
    />
  )
}
