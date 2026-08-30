/**
 * Loads the source file list and keeps it refreshable.
 *
 * The list is the join of object storage and the vector index, computed by the
 * backend on each read — so refreshing is the only way to see a file that has
 * changed in storage since the last look.
 */

import { useCallback, useEffect, useState } from 'react'

import { listSources } from '../api/client'
import type { IndexState, SourceStatus } from '../api/types'

export interface UseSourcesResult {
  sources: SourceStatus[]
  loading: boolean
  error: string | null
  /** Re-read the list from the backend. */
  refresh: () => void
  /** Replace rows in place, for the statuses an index run reports back. */
  merge: (updated: SourceStatus[]) => void
}

/** What the last settled request produced, tagged with which request it was. */
interface Result {
  /** Identifies the request these rows answer; see `requestKey` below. */
  key: string
  rows: SourceStatus[]
  error: string | null
}

const PENDING: Result = { key: '', rows: [], error: null }

/**
 * @param state - Show only files in this state, or all when undefined.
 */
export function useSources(state?: IndexState): UseSourcesResult {
  // Bumped by `refresh` to re-run the effect without changing the filter.
  const [nonce, setNonce] = useState(0)
  const [result, setResult] = useState<Result>(PENDING)

  // Identifies the request the current inputs describe. Comparing it against
  // the settled result is what makes `loading` a derived value rather than a
  // second piece of state set synchronously inside the effect.
  const requestKey = `${state ?? 'all'}:${nonce}`

  useEffect(() => {
    const abort = new AbortController()

    listSources({ state, signal: abort.signal })
      .then((rows) => {
        if (!abort.signal.aborted) setResult({ key: requestKey, rows, error: null })
      })
      .catch((cause: unknown) => {
        // An abort is a navigation or a superseded filter, not a failure.
        if (abort.signal.aborted) return
        setResult({
          key: requestKey,
          rows: [],
          error: cause instanceof Error ? cause.message : String(cause),
        })
      })

    return () => abort.abort()
  }, [state, requestKey])

  const refresh = useCallback(() => setNonce((value) => value + 1), [])

  const merge = useCallback((updated: SourceStatus[]) => {
    if (updated.length === 0) return

    setResult((current) => {
      const bySourceKey = new Map(updated.map((row) => [row.source_key, row]))
      const merged = current.rows.map((row) => bySourceKey.get(row.source_key) ?? row)

      // A run can index a file the list has never seen — append those rather
      // than dropping them until the next full refresh.
      const seen = new Set(current.rows.map((row) => row.source_key))
      const added = updated.filter((row) => !seen.has(row.source_key))

      return { ...current, rows: [...merged, ...added] }
    })
  }, [])

  return {
    sources: result.rows,
    // Still loading whenever the settled result answers a different request.
    loading: result.key !== requestKey,
    error: result.error,
    refresh,
    merge,
  }
}
