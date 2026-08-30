/**
 * Token counts and cost for one answer.
 *
 * Covers the generation call only — the embedding call made during retrieval
 * is not priced into it, which is why this is labelled as generation cost.
 */

import type { UsageEventData } from '../../api/types'

/** Costs run to fractions of a cent, so fixed decimals would round to zero. */
const MONEY = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
})

export function UsageBar({ usage }: { usage: UsageEventData }) {
  const cached = usage.cache_read_tokens > 0

  return (
    <p className="tabular mt-5 border-t border-slate-100 pt-3 text-xs text-slate-400">
      <span className="font-mono">{usage.model}</span>
      {' · '}
      {usage.input_tokens} in / {usage.output_tokens} out
      {cached ? ` · ${usage.cache_read_tokens} cached` : ''}
      {' · '}
      {MONEY.format(usage.total_cost)} generation cost
    </p>
  )
}
