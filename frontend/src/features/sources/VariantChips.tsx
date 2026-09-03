/**
 * Where a file already lives, one chip per vector space.
 *
 * Indexing moved off this screen, so its remaining job is to say where a file
 * is rather than to put it somewhere. A file can be cut four ways at once, and
 * each copy stands on its own — the one cut before the file changed is stale
 * even while the one re-cut afterwards is current, so each chip carries its own
 * state rather than borrowing the row's.
 *
 * The chip production answers from is marked, because "which of these am I
 * actually talking to" is the question this screen cannot leave unanswered.
 */

import type { SourceVariant } from '../../api/types'

interface VariantChipsProps {
  variants: SourceVariant[]
  /** Open the chunking bench on this file and variant. */
  onOpen: (variantId: string) => void
}

/** Colour by state, so a stale copy reads as stale without opening anything. */
const CHIP_TONE: Record<string, string> = {
  current: 'border-state-current-soft bg-state-current-soft text-state-current',
  stale_content: 'border-state-stale-soft bg-state-stale-soft text-state-stale',
  stale_model: 'border-state-stale-soft bg-state-stale-soft text-state-stale',
  interrupted: 'border-state-orphaned-soft bg-state-orphaned-soft text-state-orphaned',
  orphaned: 'border-state-orphaned-soft bg-state-orphaned-soft text-state-orphaned',
}

/** What each state means for this copy, said in one line on hover. */
const CHIP_REASON: Record<string, string> = {
  current: 'This copy matches the stored file.',
  stale_content: 'Cut from an older version of the file — re-index it.',
  stale_model: 'Embedded with a different model than the one configured.',
  interrupted: 'A run stopped partway; this copy is missing chunks.',
  orphaned: 'The file behind these vectors is gone.',
}

export function VariantChips({ variants, onOpen }: VariantChipsProps) {
  if (variants.length === 0) {
    return <span className="text-xs text-slate-300">—</span>
  }

  return (
    <div className="flex flex-wrap gap-1">
      {variants.map((variant) => (
        <button
          key={variant.variant_id}
          type="button"
          onClick={() => onOpen(variant.variant_id)}
          title={`${CHIP_REASON[variant.state] ?? ''} ${variant.chunk_count} chunk(s).${
            variant.active ? ' Answering questions right now.' : ''
          }`}
          className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap hover:brightness-95 ${
            CHIP_TONE[variant.state] ?? 'border-slate-200 bg-slate-100 text-slate-500'
          }`}
        >
          {variant.active ? (
            <span aria-label="Answering from this space" title="Answering from this space">
              ★
            </span>
          ) : null}
          {variant.label}
          <span className="tabular font-normal opacity-70">{variant.chunk_count}</span>
        </button>
      ))}
    </div>
  )
}
