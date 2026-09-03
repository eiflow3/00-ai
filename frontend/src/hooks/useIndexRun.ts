/**
 * Drives an indexing run and folds its event stream into progress state.
 *
 * Two things this hook has to get right, both learned the hard way:
 *
 *   1. **Starting is not streaming.** A click enqueues and gets back a run id;
 *      progress is read from that run's own stream. When the work *was* the
 *      response, reloading the page cancelled it mid-file.
 *   2. **A second click joins the run.** It no longer aborts the first stream —
 *      one worker drains one queue, so there is a single stream to follow and
 *      clicking Index on three rows queues three files.
 *
 * On mount it asks whether a run is already in flight and attaches to it, which
 * is what makes progress survive a reload. Each event carries a cursor, so a
 * dropped connection resumes rather than replaying what was already seen.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { attachIndexRun, enqueueIndex, listIndexRuns, stopIndexRun } from '../api/client'
import type {
  EnqueueResponse,
  IndexGovernanceEventData,
  IndexRequest,
  IndexStage,
  IndexSummaryEventData,
  SourceStatus,
} from '../api/types'

/** One file that failed during the run. */
export interface RunFailure {
  sourceKey: string
  stage: string
  message: string
}

export interface RunProgress {
  /** The file currently being processed. */
  sourceKey: string
  stage: IndexStage
  /** 1-based position of that file in the run. */
  fileNumber: number
  /** Files the run has taken on so far. Grows if a later click joins the run. */
  totalFiles: number
  chunkCount: number
}

export interface UseIndexRunResult {
  running: boolean
  /** The run being followed, needed to stop it. */
  jobId: string | null
  /** Null before the first `progress` event arrives. */
  progress: RunProgress | null
  /** Keys the run has taken on, in the order they were queued. */
  queued: string[]
  /** Keys still waiting their turn. */
  pending: string[]
  /** Files finished so far, successfully or as a skip. */
  finished: string[]
  failures: RunFailure[]
  /**
   * What governance found per screened file, in the order they were
   * processed. Counts only — the matched values never reach this stream.
   */
  screenings: IndexGovernanceEventData[]
  summary: IndexSummaryEventData | null
  /** Chunks the run did not have to embed, because the index already held them. */
  reused: number
  /** Keys refused because the queue is full, with the limit that refused them. */
  rejected: string[]
  limit: number
  /** A failure that ended the whole run, as opposed to one file. */
  error: string | null
  /** Whether a run in flight was found and attached to on load. */
  resumed: boolean
  enqueue: (body: IndexRequest) => Promise<EnqueueResponse | null>
  stop: () => Promise<void>
  /** Clear the last run's result so the panel can be dismissed. */
  reset: () => void
}

/**
 * @param onSettled - Called with each file's re-read status when a run ends,
 *   so the caller can refresh its list without a follow-up request.
 */
export function useIndexRun(onSettled?: (statuses: SourceStatus[]) => void): UseIndexRunResult {
  const [running, setRunning] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<RunProgress | null>(null)
  const [queued, setQueued] = useState<string[]>([])
  const [pending, setPending] = useState<string[]>([])
  const [finished, setFinished] = useState<string[]>([])
  const [failures, setFailures] = useState<RunFailure[]>([])
  const [screenings, setScreenings] = useState<IndexGovernanceEventData[]>([])
  const [summary, setSummary] = useState<IndexSummaryEventData | null>(null)
  const [reused, setReused] = useState(0)
  const [rejected, setRejected] = useState<string[]>([])
  const [limit, setLimit] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [resumed, setResumed] = useState(false)

  const controller = useRef<AbortController | null>(null)
  // The run being followed, read inside callbacks that must not re-create
  // themselves every time the id changes.
  const following = useRef<string | null>(null)
  // Latest cursor seen, so a reconnect resumes instead of replaying.
  const cursor = useRef(-1)
  // Held in a ref so `follow` does not re-create itself — and so an
  // in-flight stream keeps calling the current callback rather than the one
  // captured when it started. Assigned in an effect, never during render.
  const settled = useRef(onSettled)
  useEffect(() => {
    settled.current = onSettled
  }, [onSettled])

  /**
   * Follow a run's stream until it ends, folding events into state.
   *
   * Safe to call on a run that has already finished: the stream replays its
   * events and closes, which is how a reload rebuilds the final state.
   */
  const follow = useCallback(async (id: string, from: number) => {
    // Detaching an earlier stream no longer stops any work, so this only ever
    // ends a subscription.
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    following.current = id
    cursor.current = from

    setJobId(id)
    setRunning(true)
    setError(null)

    try {
      for await (const { event, cursor: at } of attachIndexRun(id, from, abort.signal)) {
        cursor.current = at

        switch (event.event) {
          case 'started':
            setQueued(event.data.keys)
            break

          case 'queued':
            // A later request joined this run, so the totals move.
            setQueued((current) => [...current, ...event.data.added])
            setPending(event.data.pending)
            break

          case 'progress':
            setProgress({
              sourceKey: event.data.source_key,
              stage: event.data.stage,
              fileNumber: event.data.file_number,
              totalFiles: event.data.total_files,
              chunkCount: event.data.chunk_count,
            })
            break

          case 'governance':
            setScreenings((current) => [...current, event.data])
            break

          case 'completed':
            setFinished((current) => [...current, event.data.source_key])
            setReused((current) => current + event.data.reused)
            break

          case 'error':
            // Scoped to one file; the run carries on with the rest.
            setFailures((current) => [
              ...current,
              {
                sourceKey: event.data.source_key,
                stage: event.data.stage,
                message: event.data.message,
              },
            ])
            break

          case 'summary':
            setSummary(event.data)
            settled.current?.(event.data.statuses)
            break
        }
      }
    } catch (cause: unknown) {
      if (!abort.signal.aborted) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      if (!abort.signal.aborted) {
        setRunning(false)
        setProgress(null)
        setPending([])
        following.current = null
      }
    }
  }, [])

  // On load, attach to a run already in flight. Without this a reload would
  // leave the rows reading "Indexing" with no progress behind them.
  useEffect(() => {
    let cancelled = false

    const attach = async () => {
      try {
        const runs = await listIndexRuns()
        const live = runs.find((run) => run.state === 'running')
        if (cancelled || !live) return

        setResumed(true)
        // From the beginning: this client has no history of the run, and the
        // replay is what rebuilds its progress state.
        await follow(live.job_id, -1)
      } catch {
        // Nothing to attach to, or the backend is unreachable. The list's own
        // error handling reports that; a failed probe is not worth surfacing.
      }
    }

    void attach()

    return () => {
      cancelled = true
      controller.current?.abort()
    }
  }, [follow])

  const enqueue = useCallback(
    async (body: IndexRequest): Promise<EnqueueResponse | null> => {
      setError(null)

      let response: EnqueueResponse
      try {
        response = await enqueueIndex(body)
      } catch (cause: unknown) {
        setError(cause instanceof Error ? cause.message : String(cause))
        return null
      }

      setRejected(response.rejected)
      setLimit(response.limit)
      setPending(response.pending)

      // Already following this run: the queued event will report the addition,
      // so re-attaching would only replay what is already on screen.
      if (following.current === response.job_id) {
        setQueued((current) => [
          ...current,
          ...response.accepted.filter((key) => !current.includes(key)),
        ])
        return response
      }

      // A fresh run: clear the previous one's result before following.
      setProgress(null)
      setQueued([])
      setFinished([])
      setFailures([])
      setScreenings([])
      setSummary(null)
      setReused(0)

      void follow(response.job_id, -1)
      return response
    },
    [follow],
  )

  const stop = useCallback(async () => {
    const id = following.current ?? jobId
    if (!id) return

    try {
      await stopIndexRun(id)
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [jobId])

  const reset = useCallback(() => {
    setSummary(null)
    setFailures([])
    setScreenings([])
    setFinished([])
    setQueued([])
    setPending([])
    setRejected([])
    setReused(0)
    setResumed(false)
    setError(null)
  }, [])

  return {
    running,
    jobId,
    progress,
    queued,
    pending,
    finished,
    failures,
    screenings,
    summary,
    reused,
    rejected,
    limit,
    error,
    resumed,
    enqueue,
    stop,
    reset,
  }
}
