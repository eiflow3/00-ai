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
 *
 * Moving it is not done here — that is a button on the variant you want, in the
 * table below, because choosing between variants means reading their rows. What
 * this offers is only the way *back*, and only while the original index still
 * holds something to go back to.
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
  // The original index can be retired once production points at a variant.
  // Offering a way back to an index that no longer exists would be offering an
  // action the server can only refuse.
  const canRevert = !original && production.original_vector_count > 0

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

      {canRevert ? (
        <button
          type="button"
          onClick={onReset}
          title={`The original index still holds ${production.original_vector_count} chunk(s).`}
          className="shrink-0 rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100"
        >
          Back to the original index
        </button>
      ) : (
        <p className="shrink-0 text-xs text-slate-400">
          Change it with <span className="font-medium">Answer from this</span> on
          a variant below.
        </p>
      )}
    </section>
  )
}
