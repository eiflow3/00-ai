/**
 * Live progress for an indexing run.
 *
 * The run streams because it is slow, so this shows which file is being worked
 * on and which stage it has reached. Per-file failures are listed without
 * ending the run — that is the endpoint's contract, and the panel reflects it.
 */

import type { IndexStage, IndexSummaryEventData } from '../../api/types'
import type { RunFailure, RunProgress } from '../../hooks/useIndexRun'
import { Spinner } from '../../components/Spinner'

interface IndexProgressProps {
  running: boolean
  progress: RunProgress | null
  queued: string[]
  failures: RunFailure[]
  summary: IndexSummaryEventData | null
  error: string | null
  onCancel: () => void
  onDismiss: () => void
}

/** Human wording for each pipeline stage. */
const STAGE_LABEL: Record<IndexStage, string> = {
  loading: 'Reading from storage',
  chunking: 'Splitting into chunks',
  embedding: 'Embedding',
  upserting: 'Writing to the index',
}

export function IndexProgress({
  running,
  progress,
  queued,
  failures,
  summary,
  error,
  onCancel,
  onDismiss,
}: IndexProgressProps) {
  // Nothing has happened yet and nothing is left over — render nothing.
  if (!running && !summary && !error && failures.length === 0) return null

  const total = progress?.totalFiles || queued.length
  // Before the first progress event there is no file number to report.
  const done = progress ? progress.fileNumber - 1 : summary ? total : 0
  const percent = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <section className="mb-4 rounded-lg border border-slate-200 bg-white p-4">
      <header className="flex items-center justify-between gap-4">
        <h2 className="flex items-center gap-2 text-sm font-medium text-slate-700">
          {running ? <Spinner /> : null}
          {running ? 'Indexing' : error ? 'Indexing failed' : 'Indexing finished'}
        </h2>

        {running ? (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
          >
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-md px-2.5 py-1 text-xs font-medium text-slate-400 hover:text-slate-600"
          >
            Dismiss
          </button>
        )}
      </header>

      {running && total > 0 ? (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-state-current transition-[width] duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>
          {progress ? (
            <p className="mt-2 text-xs text-slate-500">
              <span className="tabular">
                {progress.fileNumber} of {progress.totalFiles}
              </span>
              {' · '}
              <span className="font-mono">{progress.sourceKey}</span>
              {' · '}
              {STAGE_LABEL[progress.stage]}
              {progress.chunkCount > 0 ? ` · ${progress.chunkCount} chunks` : ''}
            </p>
          ) : (
            <p className="mt-2 text-xs text-slate-400">Preparing {total} file(s)…</p>
          )}
        </div>
      ) : null}

      {summary ? (
        <p className="tabular mt-3 text-xs text-slate-500">
          {summary.indexed} indexed · {summary.skipped} skipped · {summary.failed} failed ·{' '}
          {summary.total_chunks} chunks written
          {summary.total_pruned > 0 ? ` · ${summary.total_pruned} stale chunks removed` : ''}
        </p>
      ) : null}

      {error ? <p className="mt-3 text-xs text-state-orphaned">{error}</p> : null}

      {failures.length > 0 ? (
        <ul className="mt-3 space-y-1">
          {failures.map((failure, index) => (
            <li key={`${failure.sourceKey}-${index}`} className="text-xs text-state-stale">
              <span className="font-mono">{failure.sourceKey || '(no key)'}</span> —{' '}
              {failure.stage}: {failure.message}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}
