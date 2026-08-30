/**
 * Drives an indexing run and folds its event stream into progress state.
 *
 * The run is long enough that the endpoint streams rather than returning, so
 * this holds the per-file position, the current stage, and any file-level
 * failures — which are non-fatal and do not stop the run.
 */

import { useCallback, useRef, useState } from 'react'

import { indexSources } from '../api/client'
import type {
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
  totalFiles: number
  chunkCount: number
}

export interface UseIndexRunResult {
  running: boolean
  /** Null before the first `progress` event arrives. */
  progress: RunProgress | null
  /** Keys the run said it would process, known from the `started` event. */
  queued: string[]
  /** Keys another run was already embedding, so this one left them alone. */
  busy: string[]
  /** Files finished so far, successfully or as a skip. */
  finished: string[]
  failures: RunFailure[]
  summary: IndexSummaryEventData | null
  /** A failure that ended the whole run, as opposed to one file. */
  error: string | null
  start: (body: IndexRequest) => Promise<void>
  cancel: () => void
  /** Clear the last run's result so the panel can be dismissed. */
  reset: () => void
}

/**
 * @param onSettled - Called with each file's re-read status when a run ends,
 *   so the caller can refresh its list without a follow-up request.
 */
export function useIndexRun(onSettled?: (statuses: SourceStatus[]) => void): UseIndexRunResult {
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState<RunProgress | null>(null)
  const [queued, setQueued] = useState<string[]>([])
  const [busy, setBusy] = useState<string[]>([])
  const [finished, setFinished] = useState<string[]>([])
  const [failures, setFailures] = useState<RunFailure[]>([])
  const [summary, setSummary] = useState<IndexSummaryEventData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const controller = useRef<AbortController | null>(null)

  const start = useCallback(
    async (body: IndexRequest) => {
      // A second run would interleave its events with the first's.
      controller.current?.abort()
      const abort = new AbortController()
      controller.current = abort

      setRunning(true)
      setProgress(null)
      setQueued([])
      setBusy([])
      setFinished([])
      setFailures([])
      setSummary(null)
      setError(null)

      try {
        for await (const event of indexSources(body, abort.signal)) {
          switch (event.event) {
            case 'started':
              setQueued(event.data.keys)
              // Not an error: those files are being embedded by another run.
              setBusy(event.data.busy)
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

            case 'completed':
              setFinished((current) => [...current, event.data.source_key])
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
              onSettled?.(event.data.statuses)
              break
          }
        }
      } catch (cause: unknown) {
        if (!abort.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause))
        }
      } finally {
        setRunning(false)
        setProgress(null)
      }
    },
    [onSettled],
  )

  const cancel = useCallback(() => controller.current?.abort(), [])

  const reset = useCallback(() => {
    setSummary(null)
    setFailures([])
    setFinished([])
    setQueued([])
    setBusy([])
    setError(null)
  }, [])

  return {
    running,
    progress,
    queued,
    busy,
    finished,
    failures,
    summary,
    error,
    start,
    cancel,
    reset,
  }
}
