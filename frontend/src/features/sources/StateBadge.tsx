/**
 * The verdict badge for one file.
 *
 * The backend decides the state; this only chooses how to show it. Labels are
 * written from the user's point of view — "file changed" rather than the raw
 * `stale_content` — while the tooltip carries the backend's own explanation.
 */

import type { IndexState } from '../../api/types'

interface StateBadgeProps {
  state: IndexState
  /** The backend's plain-language reason, shown on hover. */
  detail?: string
}

/** Label, dot glyph and colour classes for each state. */
const PRESENTATION: Record<IndexState, { label: string; mark: string; className: string }> = {
  current: {
    label: 'Current',
    mark: '●',
    className: 'bg-state-current-soft text-state-current',
  },
  stale_content: {
    label: 'File changed',
    mark: '▲',
    className: 'bg-state-stale-soft text-state-stale',
  },
  stale_model: {
    label: 'Old model',
    mark: '▲',
    className: 'bg-state-stale-soft text-state-stale',
  },
  not_indexed: {
    label: 'Not indexed',
    mark: '○',
    className: 'bg-state-missing-soft text-state-missing',
  },
  orphaned: {
    label: 'Orphaned',
    mark: '✕',
    className: 'bg-state-orphaned-soft text-state-orphaned',
  },
  unsupported: {
    label: 'Unsupported',
    mark: '⊘',
    className: 'bg-state-missing-soft text-state-missing',
  },
}

export function StateBadge({ state, detail }: StateBadgeProps) {
  const { label, mark, className } = PRESENTATION[state]

  return (
    <span
      title={detail}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap ${className}`}
    >
      <span aria-hidden="true">{mark}</span>
      {label}
    </span>
  )
}
