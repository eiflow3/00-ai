/**
 * Drives a golden set generation run and folds its event stream into progress.
 *
 * The same shape as `useIndexRun`, and for the same reason: starting is not
 * streaming. A click posts a request and gets back a job id; progress is read
 * from that job's own stream, so closing the tab does not throw away a dozen
 * model calls. Each event carries a cursor, so a dropped connection resumes
 * rather than replaying what was already seen.
 *
 * One difference worth naming. An `error` event here is usually *not* fatal: a
 * section whose reply would not parse is reported and the run carries on, so
 * failures accumulate in a list beside the rows that did work rather than
 * replacing them.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { attachGoldenRun, startGoldenRun, stopGoldenRun } from '../api/client'
import type {
  GoldenRow,
  GoldenRunRequest,
  GoldenStage,
  GoldenSummaryEventData,
} from '../api/types'

/** One pass that failed, scoped to a section unless it stopped the run. */
export interface GoldenFailure {
  stage: GoldenStage
  /** Which section or pass failed. */
  detail: string
  message: string
}

export interface GoldenProgress {
  stage: GoldenStage
  detail: string
  completed: number
  total: number
}

export interface UseGoldenRunResult {
  running: boolean
  jobId: string | null
  /** The set being filled, so the caller can open it when the run ends. */
  setId: string | null
  /** Null until the first stage event arrives. */
  progress: GoldenProgress | null
  /** Rows as they are drafted, so a long run can be read while it goes. */
  rows: GoldenRow[]
  failures: GoldenFailure[]
  summary: GoldenSummaryEventData | null
  /** A failure that ended the whole run, as opposed to one pass. */
  error: string | null
  start: (body: GoldenRunRequest) => Promise<string | null>
  stop: () => Promise<void>
  /** Clear the last run's result so the panel can be dismissed. */
  reset: () => void
}

export function useGoldenRun(onFinished?: (setId: string) => void): UseGoldenRunResult {
  const [running, setRunning] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [setId, setSetId] = useState<string | null>(null)
  const [progress, setProgress] = useState<GoldenProgress | null>(null)
  const [rows, setRows] = useState<GoldenRow[]>([])
  const [failures, setFailures] = useState<GoldenFailure[]>([])
  const [summary, setSummary] = useState<GoldenSummaryEventData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const controller = useRef<AbortController | null>(null)
  const cursor = useRef(-1)
  // Held in a ref so `follow` does not re-create itself, and so an in-flight
  // stream calls the current callback rather than the one captured when it
  // started. Assigned in an effect, never during render.
  const finished = useRef(onFinished)
  useEffect(() => {
    finished.current = onFinished
  }, [onFinished])

  /** Follow a run's stream until it ends, folding events into state. */
  const follow = useCallback(async (id: string, from: number) => {
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    cursor.current = from

    setJobId(id)
    setRunning(true)
    setError(null)

    try {
      for await (const { event, cursor: at } of attachGoldenRun(id, from, abort.signal)) {
        cursor.current = at

        switch (event.event) {
          case 'started':
            setSetId(event.data.set_id)
            break

          case 'stage':
            setProgress({
              stage: event.data.stage,
              detail: event.data.detail,
              completed: event.data.completed,
              total: event.data.total,
            })
            break

          case 'row':
            setRows((current) => [...current, event.data.row])
            break

          case 'error':
            if (event.data.fatal) {
              setError(event.data.message)
              break
            }
            // Scoped to one pass; the run carries on with the rest.
            setFailures((current) => [
              ...current,
              {
                stage: event.data.stage,
                detail: event.data.detail,
                message: event.data.message,
              },
            ])
            break

          case 'summary':
            setSummary(event.data)
            finished.current?.(event.data.set_id)
            break
        }
      }
    } catch (cause: unknown) {
      // An abort is this hook detaching, not a failure of the run.
      if (!abort.signal.aborted) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      if (!abort.signal.aborted) setRunning(false)
    }
  }, [])

  const start = useCallback(
    async (body: GoldenRunRequest): Promise<string | null> => {
      setProgress(null)
      setRows([])
      setFailures([])
      setSummary(null)
      setError(null)

      try {
        const started = await startGoldenRun(body)
        setSetId(started.set_id)
        void follow(started.job_id, -1)
        return null
      } catch (cause: unknown) {
        const message = cause instanceof Error ? cause.message : String(cause)
        setError(message)
        return message
      }
    },
    [follow],
  )

  const stop = useCallback(async () => {
    if (jobId === null) return
    try {
      await stopGoldenRun(jobId)
    } catch {
      // The run may already have finished; nothing here depends on it.
    }
  }, [jobId])

  const reset = useCallback(() => {
    controller.current?.abort()
    setRunning(false)
    setJobId(null)
    setProgress(null)
    setRows([])
    setFailures([])
    setSummary(null)
    setError(null)
  }, [])

  // Detach on unmount. The run keeps going: it is a task on the server, not
  // part of this component's lifetime, which is the whole point of the split.
  useEffect(() => () => controller.current?.abort(), [])

  return {
    running,
    jobId,
    setId,
    progress,
    rows,
    failures,
    summary,
    error,
    start,
    stop,
    reset,
  }
}
