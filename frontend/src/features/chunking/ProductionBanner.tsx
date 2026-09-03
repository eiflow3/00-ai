/**
 * Which space the app answers from, stated where you can change it.
 *
 * Production is a pointer rather than a place, so this is the line that turns a
 * comparison into a decision: whichever variant wins the scoreboard can become
 * the default answer, instantly and reversibly, because its vectors already
 * exist.
 *
 * Stated permanently rather than only while it is unusual. Once the answering
 * space is a setting, "which cut am I actually talking to" stops being obvious,
 * and a screen that only mentions it when something is wrong teaches nobody
 * where their answers come from.
 */

import type { ProductionSpace } from '../../api/types'
import { RelativeTime } from '../../components/RelativeTime'
import { Spinner } from '../../components/Spinner'

interface ProductionBannerProps {
  production: ProductionSpace | null
  loading: boolean
  error: string | null
  /** Point production back at the original index. */
  onReset: () => void
}

export function ProductionBanner({
  production,
  loading,
  error,
  onReset,
}: ProductionBannerProps) {
  if (error !== null) {
    return (
      <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
        {error}
      </p>
    )
  }

  if (production === null) {
    return (
      <p className="mb-4 flex items-center gap-2 px-1 text-sm text-slate-400">
        {loading ? <Spinner /> : null}
        Reading where answers come from…
      </p>
    )
  }

  // A pointer at a namespace that has since been emptied. Said plainly: nothing
  // falls back on its own, so questions asked now come back ungrounded.
  const missing = production.state === 'missing'
  const original = production.variant_id === ''

  return (
    <section
      className={`mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3 ${
        missing
          ? 'border-state-orphaned-soft bg-state-orphaned-soft'
          : 'border-slate-200 bg-white'
      }`}
    >
      <div className="text-sm">
        <span className="text-slate-500">Answering from</span>{' '}
        <span className="font-medium text-slate-900">{production.label}</span>
        {missing ? (
          <p className="mt-1 text-xs text-state-orphaned">
            That space holds no vectors any more, so questions come back
            ungrounded. Point production somewhere that does.
          </p>
        ) : (
          <p className="mt-1 text-xs text-slate-500">
            {production.vector_count} chunk(s) across{' '}
            {production.source_keys.length} file(s)
            {production.updated_at ? (
              <>
                {' · adopted '}
                <RelativeTime value={production.updated_at} />
              </>
            ) : null}
          </p>
        )}
      </div>

      {original ? null : (
        <button
          type="button"
          onClick={onReset}
          className="shrink-0 rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100"
        >
          Back to the original index
        </button>
      )}
    </section>
  )
}
