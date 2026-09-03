/**
 * The variants that exist right now, read back from the index itself.
 *
 * A row is a way of cutting a document that has actually been embedded and can
 * therefore be asked a question. A variant reporting `interrupted` holds fewer
 * vectors than its last run said it should, and is called out rather than
 * quietly listed: scoring it would blame the strategy for text that was never
 * embedded.
 *
 * Adopting a row is what makes the comparison worth running: production is a
 * pointer, so the winner becomes the default answer with nothing re-embedded.
 * The adopted row cannot be deleted from here — pointing production elsewhere
 * first is how that decision gets made rather than stumbled into.
 */

import type { ChunkVariant } from '../../api/types'
import { EmptyState } from '../../components/EmptyState'
import { RelativeTime } from '../../components/RelativeTime'
import { Spinner } from '../../components/Spinner'

interface VariantsTableProps {
  variants: ChunkVariant[]
  loading: boolean
  deleting: string | null
  /** The variant production currently answers from. */
  active: string | null
  /** The variant being adopted right now, so its row can show it. */
  pointing: string | null
  onAsk: (variantId: string) => void
  onAdopt: (variantId: string) => void
  onDelete: (variant: ChunkVariant) => void
}

export function VariantsTable({
  variants,
  loading,
  deleting,
  active,
  pointing,
  onAsk,
  onAdopt,
  onDelete,
}: VariantsTableProps) {
  if (loading && variants.length === 0) {
    return (
      <p className="flex items-center gap-2 px-1 py-8 text-sm text-slate-400">
        <Spinner />
        Reading what is embedded…
      </p>
    )
  }

  if (variants.length === 0) {
    return (
      <EmptyState
        title="Nothing cut yet"
        hint="Pick a file above and preview a strategy — previewing costs nothing."
      />
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-3xl border-collapse">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs font-medium tracking-wide text-slate-400 uppercase">
            <th className="py-2.5 pr-3 pl-4">Variant</th>
            <th className="px-3 py-2.5">File</th>
            <th className="px-3 py-2.5 text-right">Chunks</th>
            <th className="px-3 py-2.5">Embedded</th>
            <th className="py-2.5 pr-4 pl-3" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {variants.map((variant) => (
            <tr
              key={variant.variant_id}
              className={`text-sm ${
                variant.variant_id === active ? 'bg-state-current-soft/40' : ''
              }`}
            >
              <td className="py-2.5 pr-3 pl-4">
                <span className="font-medium text-slate-900">{variant.label}</span>
                {variant.variant_id === active ? (
                  <span
                    title="Every question with no variant named is answered from here."
                    className="ml-2 rounded-full bg-state-current-soft px-2 py-0.5 text-xs font-medium text-state-current"
                  >
                    answering
                  </span>
                ) : null}
                {variant.state === 'interrupted' ? (
                  <span className="ml-2 rounded-full bg-state-stale-soft px-2 py-0.5 text-xs font-medium text-state-stale">
                    part embedded
                  </span>
                ) : null}
              </td>
              <td className="px-3 py-2.5 text-slate-600">
                {variant.source_keys.join(', ') || '—'}
              </td>
              <td className="tabular px-3 py-2.5 text-right text-slate-700">
                {variant.vector_count}
                {variant.state === 'interrupted' ? (
                  <span className="text-state-stale"> / {variant.chunk_total}</span>
                ) : null}
              </td>
              <td className="px-3 py-2.5 text-slate-500">
                <RelativeTime value={variant.embedded_at} />
              </td>
              <td className="py-2.5 pr-4 pl-3 text-right">
                <button
                  type="button"
                  onClick={() => onAsk(variant.variant_id)}
                  className="mr-2 rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
                >
                  Ask
                </button>
                {variant.variant_id === active ? null : (
                  <button
                    type="button"
                    onClick={() => onAdopt(variant.variant_id)}
                    disabled={
                      pointing !== null || variant.state === 'interrupted'
                    }
                    title={
                      variant.state === 'interrupted'
                        ? 'This copy is incomplete, so it would answer with gaps.'
                        : 'Make this the space every answer comes from.'
                    }
                    className="mr-2 rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-40"
                  >
                    {pointing === variant.variant_id ? 'Adopting…' : 'Answer from this'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onDelete(variant)}
                  disabled={
                    deleting === variant.variant_id ||
                    variant.variant_id === active
                  }
                  title={
                    variant.variant_id === active
                      ? 'Point production elsewhere before deleting this.'
                      : undefined
                  }
                  className="text-xs text-slate-400 hover:text-state-orphaned disabled:opacity-40"
                >
                  {deleting === variant.variant_id ? 'Deleting…' : 'Delete'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
