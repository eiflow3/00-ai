/**
 * Live progress for an indexing run.
 *
 * The run belongs to the server, not to this page, so three things have to be
 * visible that a page-owned run never needed to show:
 *
 *   * that progress was *resumed* — the run was already going when the page
 *     loaded, rather than started by this click;
 *   * that the total can grow, because a later click joins the run in flight;
 *   * that Stop is now the only way to end a run, since closing the tab no
 *     longer does it.
 *
 * Per-file failures are listed without ending the run — that is the pipeline's
 * contract, and the panel reflects it.
 */

import type {
  IndexGovernanceEventData,
  IndexStage,
  IndexSummaryEventData,
} from '../../api/types'
import type { RunFailure, RunProgress } from '../../hooks/useIndexRun'
import { Spinner } from '../../components/Spinner'

interface IndexProgressProps {
  running: boolean
  progress: RunProgress | null
  queued: string[]
  /** Keys still waiting their turn behind the file being embedded. */
  pending: string[]
  failures: RunFailure[]
  /** What governance found per screened file. Counts only, never values. */
  screenings: IndexGovernanceEventData[]
  summary: IndexSummaryEventData | null
  /** Chunks the run did not have to embed, because the index already held them. */
  reused: number
  /** Keys refused because the queue is full, and the limit that refused them. */
  rejected: string[]
  limit: number
  error: string | null
  /** True when this page attached to a run that was already in flight. */
  resumed: boolean
  onStop: () => void
  onDismiss: () => void
}

/** Human wording for each pipeline stage. */
const STAGE_LABEL: Record<IndexStage, string> = {
  loading: 'Reading from storage',
  extracting: 'Extracting text',
  describing_tables: 'Describing tables',
  screening: 'Screening for sensitive data',
  chunking: 'Splitting into chunks',
  embedding: 'Embedding',
  upserting: 'Writing to the index',
}

/** "2× email (personal, mask)" — one screening's findings, readably. */
function screeningLine(screening: IndexGovernanceEventData): string {
  if (!screening.screened) return 'indexed unscreened — governance was off'
  if (screening.verdict === 'blocked') return 'refused by governance policy'
  if (screening.findings.length === 0) return 'screened, no findings'
  return screening.findings
    .map((finding) => {
      const kind = finding.entity_type.replace(/_/g, ' ')
      const clause = [finding.classification, finding.action].filter(Boolean).join(', ')
      return `${finding.count}× ${kind}${clause ? ` (${clause})` : ''}`
    })
    .join(' · ')
}

export function IndexProgress({
  running,
  progress,
  queued,
  pending,
  failures,
  screenings,
  summary,
  reused,
  rejected,
  limit,
  error,
  resumed,
  onStop,
  onDismiss,
}: IndexProgressProps) {
  // Nothing has happened yet and nothing is left over — render nothing.
  if (!running && !summary && !error && failures.length === 0 && rejected.length === 0) {
    return null
  }

  const total = progress?.totalFiles || queued.length
  // Before the first progress event there is no file number to report.
  const done = progress ? progress.fileNumber - 1 : summary ? total : 0
  const percent = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <section className="mb-4 rounded-lg border border-slate-200 bg-white p-4">
      <header className="flex items-center justify-between gap-4">
        <h2 className="flex items-center gap-2 text-sm font-medium text-slate-700">
          {running ? <Spinner /> : null}
          {running
            ? resumed && !summary
              ? 'Indexing — rejoined a run already in progress'
              : 'Indexing'
            : error
              ? 'Indexing failed'
              : 'Indexing finished'}
        </h2>

        {running ? (
          <button
            type="button"
            onClick={onStop}
            title="The run continues on the server until stopped, even if you close this tab."
            className="rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
          >
            Stop
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
          {pending.length > 0 ? (
            <p className="mt-1 text-xs text-slate-400">
              Waiting: <span className="font-mono">{pending.join(', ')}</span>
            </p>
          ) : null}
        </div>
      ) : null}

      {summary ? (
        <p className="tabular mt-3 text-xs text-slate-500">
          {summary.indexed} indexed · {summary.skipped} skipped · {summary.failed} failed ·{' '}
          {summary.total_chunks} chunks
          {summary.total_reused > 0
            ? ` · ${summary.total_reused} reused without re-embedding`
            : ''}
          {summary.total_pruned > 0 ? ` · ${summary.total_pruned} stale chunks removed` : ''}
        </p>
      ) : null}

      {running && reused > 0 ? (
        <p className="tabular mt-3 text-xs text-slate-500">
          {reused} chunk(s) already in the index and reused, so not embedded again.
        </p>
      ) : null}

      {error ? <p className="mt-3 text-xs text-state-orphaned">{error}</p> : null}

      {rejected.length > 0 ? (
        <p className="mt-3 text-xs text-state-stale">
          Not queued — the limit of {limit} waiting file(s) is reached:{' '}
          <span className="font-mono">{rejected.join(', ')}</span>
        </p>
      ) : null}

      {screenings.length > 0 ? (
        <ul className="mt-3 space-y-1">
          {screenings.map((screening, index) => (
            <li
              key={`${screening.source_key}-${index}`}
              className={`text-xs ${
                screening.screened && screening.verdict === 'allowed'
                  ? 'text-slate-400'
                  : 'text-state-stale'
              }`}
            >
              <span className="font-mono">{screening.source_key}</span> —{' '}
              {screeningLine(screening)}
            </li>
          ))}
        </ul>
      ) : null}

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
