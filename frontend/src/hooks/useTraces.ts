/**
 * Loads recorded chat requests and keeps the list refreshable.
 *
 * The filters are part of the request rather than applied here, because the
 * judgement filters ("show me what I rated badly") depend on rows in another
 * table — only the backend can answer them without loading everything.
 */

import { useCallback, useEffect, useState } from 'react'

import { listTraces } from '../api/client'
import type { Trace, Verdict } from '../api/types'

/** What the list is currently showing. */
export interface TraceFilters {
  /** True for judged requests only, false for the unjudged backlog. */
  evaluated?: boolean
  verdict?: Verdict
  model?: string
  search?: string
}

export interface UseTracesResult {
  traces: Trace[]
  total: number
  loading: boolean
  error: string | null
  /** Re-read the current page. */
  refresh: () => void
  /** Drop one row locally, so a delete does not need a round trip to show. */
  forget: (traceId: string) => void
  /** Replace one row's rollup after it has been judged. */
  merge: (trace: Trace) => void
}

/** Rows fetched at once. Enough to scan a week of questions without paging. */
const PAGE_SIZE = 50

export function useTraces(filters: TraceFilters): UseTracesResult {
  // Bumped by `refresh` to re-run the effect without changing the filters.
  const [nonce, setNonce] = useState(0)
  const [traces, setTraces] = useState<Trace[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)

  // Identifies the request the current inputs describe, so `loading` stays a
  // derived value rather than a second piece of state.
  const requestKey = JSON.stringify({ ...filters, nonce })
  const [settled, setSettled] = useState('')

  useEffect(() => {
    const abort = new AbortController()

    listTraces({ ...filters, limit: PAGE_SIZE, signal: abort.signal })
      .then((page) => {
        if (abort.signal.aborted) return
        setTraces(page.traces)
        setTotal(page.total)
        setError(null)
        setSettled(requestKey)
      })
      .catch((cause: unknown) => {
        // An abort is a changed filter, not a failure.
        if (abort.signal.aborted) return
        setTraces([])
        setTotal(0)
        setError(cause instanceof Error ? cause.message : String(cause))
        setSettled(requestKey)
      })

    return () => abort.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey])

  const refresh = useCallback(() => setNonce((value) => value + 1), [])

  const forget = useCallback((traceId: string) => {
    setTraces((current) => current.filter((trace) => trace.trace_id !== traceId))
    setTotal((current) => Math.max(0, current - 1))
  }, [])

  const merge = useCallback((updated: Trace) => {
    setTraces((current) =>
      current.map((trace) => (trace.trace_id === updated.trace_id ? updated : trace)),
    )
  }, [])

  return {
    traces,
    total,
    loading: settled !== requestKey,
    error,
    refresh,
    forget,
    merge,
  }
}
