/**
 * Drives a comparison run and folds its event stream into a scoreboard.
 *
 * The same shape as `useIndexRun`, for the same reason: starting is not
 * streaming. A click starts the run and gets an id back; progress is read from
 * that run's own stream, so closing the tab does not throw away eighty
 * retrievals, and reopening it picks the run back up.
 *
 * Results land twice over. Each question arrives as it is answered, which is
 * what makes a run that takes a minute feel like it is doing something, and
 * each variant arrives complete when it finishes. The closing summary ranks
 * them, and that ranking is the answer the screen exists for.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { attachVariantScore, startVariantScore, stopVariantScore } from '../api/client'
import type {
  RowScore,
  ScoreStartedEventData,
  ScoreSummaryEventData,
  VariantScore,
  VariantScoreRequest,
} from '../api/types'

/** How far one variant has got, before it has finished the set. */
export interface VariantProgress {
  variantId: string
  completed: number
  /** The rows scored so far, in the order they were asked. */
  scores: RowScore[]
}

export interface UseVariantScoreResult {
  running: boolean
  jobId: string | null
  /** The run's scope, known as soon as it starts. */
  started: ScoreStartedEventData | null
  /** Questions answered across every variant, against the run's total. */
  completed: number
  total: number
  /** Per-variant progress while the run is in flight. */
  progress: Record<string, VariantProgress>
  /** Variants that have finished the whole set, in the order they finished. */
  finished: VariantScore[]
  /** The ranking, present only once the run has closed. */
  summary: ScoreSummaryEventData | null
  failures: string[]
  error: string | null
  start: (body: VariantScoreRequest) => Promise<void>
  stop: () => Promise<void>
  reset: () => void
}

export function useVariantScore(): UseVariantScoreResult {
  const [running, setRunning] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [started, setStarted] = useState<ScoreStartedEventData | null>(null)
  const [completed, setCompleted] = useState(0)
  const [total, setTotal] = useState(0)
  const [progress, setProgress] = useState<Record<string, VariantProgress>>({})
  const [finished, setFinished] = useState<VariantScore[]>([])
  const [summary, setSummary] = useState<ScoreSummaryEventData | null>(null)
  const [failures, setFailures] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const controller = useRef<AbortController | null>(null)

  // Detaching on unmount only. The run itself is unaffected — that is the
  // point of it living on the server rather than in this component.
  useEffect(() => () => controller.current?.abort(), [])

  const reset = useCallback(() => {
    setStarted(null)
    setCompleted(0)
    setTotal(0)
    setProgress({})
    setFinished([])
    setSummary(null)
    setFailures([])
    setError(null)
    setJobId(null)
  }, [])

  const follow = useCallback(async (id: string) => {
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort

    try {
      for await (const { event } of attachVariantScore(id, -1, abort.signal)) {
        switch (event.event) {
          case 'started':
            setStarted(event.data)
            setTotal(event.data.rows * event.data.variants.length)
            break

          case 'progress': {
            const { variant_id: variantId, score, completed: done, total: all } = event.data
            setCompleted(done)
            setTotal(all)
            setProgress((current) => {
              const seen = current[variantId] ?? {
                variantId,
                completed: 0,
                scores: [],
              }
              return {
                ...current,
                [variantId]: {
                  variantId,
                  completed: seen.completed + 1,
                  scores: [...seen.scores, score],
                },
              }
            })
            break
          }

          case 'variant':
            setFinished((current) => [...current, event.data])
            break

          case 'error':
            // A variant or a row that failed. The run continues without it, so
            // this is a note beside the table rather than an error instead of
            // one.
            setFailures((current) => [...current, event.data.message])
            break

          case 'summary':
            setSummary(event.data)
            break
        }
      }
    } catch (cause: unknown) {
      if (!abort.signal.aborted) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      if (controller.current === abort) setRunning(false)
    }
  }, [])

  const start = useCallback(
    async (body: VariantScoreRequest) => {
      reset()
      setRunning(true)

      try {
        const response = await startVariantScore(body)
        setJobId(response.job_id)
        setTotal(response.rows * response.variants.length)
        await follow(response.job_id)
      } catch (cause: unknown) {
        setError(cause instanceof Error ? cause.message : String(cause))
        setRunning(false)
      }
    },
    [follow, reset],
  )

  const stop = useCallback(async () => {
    if (jobId === null) return
    try {
      await stopVariantScore(jobId)
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [jobId])

  return {
    running,
    jobId,
    started,
    completed,
    total,
    progress,
    finished,
    summary,
    failures,
    error,
    start,
    stop,
    reset,
  }
}
