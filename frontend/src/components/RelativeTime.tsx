/**
 * A timestamp shown as a readable date, with the elapsed time on hover.
 *
 * Both sides of the pipeline are compared by date on this screen, so the two
 * timestamps must be formatted identically to be comparable at a glance.
 */

interface RelativeTimeProps {
  /** ISO timestamp, or null when the underlying record does not exist. */
  value: string | null | undefined
  /** Shown in place of a date when there is no value. */
  fallback?: string
}

const FORMAT = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

/** Divisors for turning an elapsed millisecond count into a sensible unit. */
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 60 * 60 * 1000],
  ['month', 30 * 24 * 60 * 60 * 1000],
  ['day', 24 * 60 * 60 * 1000],
  ['hour', 60 * 60 * 1000],
  ['minute', 60 * 1000],
]

/** Describe how long ago a moment was, in its largest meaningful unit. */
function describeElapsed(date: Date): string {
  const elapsed = date.getTime() - Date.now()

  for (const [unit, size] of UNITS) {
    if (Math.abs(elapsed) >= size) {
      return RELATIVE.format(Math.round(elapsed / size), unit)
    }
  }
  return 'just now'
}

export function RelativeTime({ value, fallback = '—' }: RelativeTimeProps) {
  if (!value) {
    return (
      <span className="text-slate-300" title="No record on this side">
        {fallback}
      </span>
    )
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return <span className="text-slate-300">{fallback}</span>
  }

  return (
    <time dateTime={value} title={date.toLocaleString()} className="tabular text-slate-700">
      {FORMAT.format(date)}
      <span className="ml-1.5 text-xs text-slate-400">{describeElapsed(date)}</span>
    </time>
  )
}
