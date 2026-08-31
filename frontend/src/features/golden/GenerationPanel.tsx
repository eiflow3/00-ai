/**
 * Starts a generation run and reports it while it drafts.
 *
 * The source picker lists indexed and unindexed files alike: generation reads
 * the file straight from storage, so a document does not have to be embedded to
 * be asked questions about. Indexing decides what can be retrieved; a golden
 * set is about what the document says.
 *
 * How many questions get asked is not offered as a setting, because it should
 * not be one — the quota comes from the document, and a model told to produce
 * forty questions from a page that supports six will invent the other
 * thirty-four. Density nudges that, and nothing more.
 */

import { useState } from 'react'

import { Spinner } from '../../components/Spinner'
import type { GoldenSummaryEventData, SourceStatus } from '../../api/types'
import type { GoldenFailure, GoldenProgress } from '../../hooks/useGoldenRun'

interface GenerationPanelProps {
  sources: SourceStatus[]
  running: boolean
  progress: GoldenProgress | null
  rowCount: number
  failures: GoldenFailure[]
  summary: GoldenSummaryEventData | null
  error: string | null
  onStart: (sourceKey: string, density: number) => void
  onStop: () => void
  onDismiss: () => void
  onOpenSet: (setId: string) => void
}

/** What each stage is doing, in a person's words. */
const STAGE_LABELS: Record<string, string> = {
  extract: 'Reading the file',
  segment: 'Finding its sections',
  facts: 'Indexing what it states',
  draft: 'Drafting questions',
  validate: 'Checking every claim against the document',
  self_check: 'Scoring each row against its own answer',
}

export function GenerationPanel({
  sources,
  running,
  progress,
  rowCount,
  failures,
  summary,
  error,
  onStart,
  onStop,
  onDismiss,
  onOpenSet,
}: GenerationPanelProps) {
  const [sourceKey, setSourceKey] = useState('')
  const [density, setDensity] = useState(1)

  // `orphaned` means the vectors outlived the file, so there is nothing left to
  // read; `unsupported` means we cannot decode it. Neither can be drafted from.
  const readable = sources.filter(
    (source) => source.state !== 'unsupported' && source.state !== 'orphaned',
  )

  return (
    <section className="mb-6 rounded-lg border border-slate-200 bg-white px-4 py-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex-1">
          <span className="mb-1 block text-xs font-medium text-slate-600">Source file</span>
          <select
            value={sourceKey}
            onChange={(event) => setSourceKey(event.target.value)}
            disabled={running}
            className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">Choose a file…</option>
            {readable.map((source) => (
              <option key={source.source_key} value={source.source_key}>
                {source.source_key}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span className="mb-1 block text-xs font-medium text-slate-600">
            Density — {density.toFixed(1)}×
          </span>
          <input
            type="range"
            min={0.5}
            max={2}
            step={0.1}
            value={density}
            onChange={(event) => setDensity(Number(event.target.value))}
            disabled={running}
            className="w-32"
          />
        </label>

        <button
          type="button"
          onClick={() => onStart(sourceKey, density)}
          disabled={running || sourceKey === ''}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          Generate
        </button>

        {running ? (
          <button
            type="button"
            onClick={onStop}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700"
          >
            Stop
          </button>
        ) : null}
      </div>

      <p className="mt-2 text-xs text-slate-400">
        The file does not need to be indexed. How many questions get asked is
        computed from the document — section length sets the per-section quota,
        and a document stating too few figures is not asked for arithmetic at all.
      </p>

      {running || progress !== null ? (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <p className="flex items-center gap-2 text-sm text-slate-700">
            {running ? <Spinner /> : null}
            {progress === null ? 'Starting…' : STAGE_LABELS[progress.stage] ?? progress.stage}
            {progress !== null && progress.total > 0 ? (
              <span className="text-slate-400">
                {progress.completed}/{progress.total}
              </span>
            ) : null}
          </p>
          {progress !== null && progress.detail !== '' ? (
            <p className="mt-0.5 truncate text-xs text-slate-400">{progress.detail}</p>
          ) : null}
          {rowCount > 0 ? (
            <p className="mt-1 text-xs text-slate-500">{rowCount} row(s) drafted so far</p>
          ) : null}
        </div>
      ) : null}

      {failures.length > 0 ? (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <p className="text-xs font-medium text-state-stale">
            {failures.length} pass(es) failed. The rest of the run carried on.
          </p>
          <ul className="mt-1">
            {failures.map((failure, index) => (
              <li key={`${failure.detail}-${index}`} className="text-xs text-slate-500">
                {failure.detail} — {failure.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {error !== null ? (
        <p className="mt-3 rounded border border-state-orphaned-soft bg-state-orphaned-soft px-3 py-2 text-sm text-state-orphaned">
          The run stopped — {error}
        </p>
      ) : null}

      {summary !== null ? (
        <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-3">
          <p className="text-sm text-slate-700">
            Drafted <strong>{summary.row_count}</strong> rows —{' '}
            <span className="text-state-current">{summary.valid_count} grounded</span>
            {summary.flagged_count > 0 ? (
              <>
                , <span className="text-state-stale">{summary.flagged_count} to check</span>
              </>
            ) : null}
            .
          </p>
          <button
            type="button"
            onClick={() => onOpenSet(summary.set_id)}
            className="rounded bg-slate-900 px-3 py-1.5 text-xs font-medium text-white"
          >
            Review it
          </button>
          <button
            type="button"
            onClick={onDismiss}
            className="text-xs text-slate-400 hover:text-slate-600"
          >
            Dismiss
          </button>
        </div>
      ) : null}
    </section>
  )
}
