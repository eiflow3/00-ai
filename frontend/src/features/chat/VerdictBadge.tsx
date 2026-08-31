/**
 * A verdict, shown the same way everywhere it appears.
 *
 * Retrieval and generation verdicts sit side by side on a row, so they have to
 * be distinguishable at a glance without reading the label.
 */

import type { EvaluationTarget, Verdict } from '../../api/types'

interface VerdictBadgeProps {
  target: EvaluationTarget
  verdict: Verdict
  /** Dimmed, for a judgement that has been withdrawn. */
  muted?: boolean
}

/** Colour by verdict, reusing the state tokens the sources screen already uses. */
const TONE: Record<Verdict, string> = {
  good: 'border-state-current-soft bg-state-current-soft text-state-current',
  partial: 'border-state-stale-soft bg-state-stale-soft text-state-stale',
  bad: 'border-state-orphaned-soft bg-state-orphaned-soft text-state-orphaned',
}

/** The stage names, shortened for a row where space is tight. */
const TARGET_LABEL: Record<EvaluationTarget, string> = {
  retrieval: 'Retrieval',
  generation: 'Answer',
  overall: 'Overall',
}

export function VerdictBadge({ target, verdict, muted = false }: VerdictBadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium ${
        muted ? 'border-slate-200 bg-slate-50 text-slate-400 line-through' : TONE[verdict]
      }`}
    >
      <span className="opacity-70">{TARGET_LABEL[target]}</span>
      {verdict}
    </span>
  )
}

export { TARGET_LABEL }
