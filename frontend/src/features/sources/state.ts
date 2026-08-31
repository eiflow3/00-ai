/**
 * Rules for reading a file's index state.
 *
 * Kept apart from the badge component so a module can ask about state without
 * importing UI — and so the badge file exports only components.
 */

import type { IndexState } from '../../api/types'

/** States a re-index can actually resolve. */
const REINDEXABLE: ReadonlySet<IndexState> = new Set<IndexState>([
  'not_indexed',
  'stale_content',
  'stale_model',
  'interrupted',
])

/**
 * Whether re-running the pipeline on this file would change anything.
 *
 * `orphaned` is excluded: there is no longer a file to embed, so the
 * resolution is deleting its vectors instead.
 */
export function needsReindex(state: IndexState): boolean {
  return REINDEXABLE.has(state)
}
