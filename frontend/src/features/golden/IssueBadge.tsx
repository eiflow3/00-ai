/**
 * The verdict badge for one drafted row.
 *
 * A flagged row is not a rejected one. Most flags are a good question with one
 * bad field — a paraphrased answer key, a wrong section cited — so the badge
 * carries a count and the reasons sit next to it, rather than the row being
 * hidden or deleted.
 */

import { checkLabel } from './checks'
import type { GoldenIssue, GoldenReview, GoldenRowStatus } from '../../api/types'

interface IssueBadgeProps {
  status: GoldenRowStatus
  issues: GoldenIssue[]
}

export function IssueBadge({ status, issues }: IssueBadgeProps) {
  if (status === 'valid') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-state-current-soft px-2.5 py-1 text-xs font-medium whitespace-nowrap text-state-current">
        <span aria-hidden="true">●</span>
        Grounded
      </span>
    )
  }

  return (
    <span
      title={issues.map((issue) => `${checkLabel(issue.check)}: ${issue.detail}`).join('\n')}
      className="inline-flex items-center gap-1.5 rounded-full bg-state-stale-soft px-2.5 py-1 text-xs font-medium whitespace-nowrap text-state-stale"
    >
      <span aria-hidden="true">▲</span>
      {issues.length} to check
    </span>
  )
}

/** The badge for what a person decided about a row. */
export function ReviewBadge({ review }: { review: GoldenReview }) {
  if (review === 'pending') return null

  const accepted = review === 'accepted'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap ${
        accepted
          ? 'bg-state-current-soft text-state-current'
          : 'bg-state-missing-soft text-state-missing'
      }`}
    >
      <span aria-hidden="true">{accepted ? '✓' : '✕'}</span>
      {accepted ? 'Accepted' : 'Dropped'}
    </span>
  )
}
