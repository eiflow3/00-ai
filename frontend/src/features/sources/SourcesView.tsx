/**
 * The sources screen: every file in storage, joined with its embeddings.
 *
 * This is the answer to "the file changed, but did its embeddings?" — the two
 * timestamps are columns of the same row, and the state column says which way
 * the comparison came out.
 */

import { useCallback, useMemo, useState } from 'react'

import { IndexProgress } from './IndexProgress'
import { SourceRow } from './SourceRow'
import { needsReindex } from './state'
import { deindexSource } from '../../api/client'
import type { IndexState } from '../../api/types'
import { EmptyState } from '../../components/EmptyState'
import { Spinner } from '../../components/Spinner'
import { useIndexRun } from '../../hooks/useIndexRun'
import { useSources } from '../../hooks/useSources'

/** The filter options, in the order they read most naturally. */
const FILTERS: { value: IndexState | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'stale_content', label: 'Changed' },
  { value: 'not_indexed', label: 'Not indexed' },
  { value: 'current', label: 'Current' },
  { value: 'orphaned', label: 'Orphaned' },
]

export function SourcesView() {
  const [filter, setFilter] = useState<IndexState | 'all'>('all')
  const [expanded, setExpanded] = useState<string | null>(null)

  const { sources, loading, error, refresh, merge } = useSources(
    filter === 'all' ? undefined : filter,
  )

  // A finished run reports each file's re-read status, so the table updates
  // from the stream itself rather than issuing another list request.
  const run = useIndexRun(merge)

  const staleCount = useMemo(
    () => sources.filter((status) => needsReindex(status.state)).length,
    [sources],
  )

  const toggle = useCallback((sourceKey: string) => {
    setExpanded((current) => (current === sourceKey ? null : sourceKey))
  }, [])

  const handleDeindex = useCallback(
    async (sourceKey: string) => {
      await deindexSource(sourceKey)
      // Deletion changes which side knows about the file, so re-read the join.
      refresh()
    },
    [refresh],
  )

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Sources</h1>
          <p className="mt-1 text-sm text-slate-500">
            Files in object storage, next to what the vector index holds for them.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={() => run.start({ only_stale: true })}
            disabled={run.running || staleCount === 0}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-30"
          >
            Index {staleCount > 0 ? `${staleCount} ` : ''}stale
          </button>
        </div>
      </header>

      <nav className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setFilter(option.value)}
            className={`rounded-full px-3 py-1 text-xs font-medium ${
              filter === option.value
                ? 'bg-slate-900 text-white'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            {option.label}
          </button>
        ))}
      </nav>

      <IndexProgress
        running={run.running}
        progress={run.progress}
        queued={run.queued}
        failures={run.failures}
        summary={run.summary}
        error={run.error}
        onCancel={run.cancel}
        onDismiss={run.reset}
      />

      {error ? (
        <p className="rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {error}
        </p>
      ) : loading ? (
        <p className="flex items-center gap-2 px-1 py-8 text-sm text-slate-400">
          <Spinner />
          Loading sources…
        </p>
      ) : sources.length === 0 ? (
        <EmptyState
          title={filter === 'all' ? 'No files in object storage' : 'No files in this state'}
          hint={
            filter === 'all'
              ? 'Upload a .txt or .md file to the bucket, then refresh.'
              : 'Try a different filter.'
          }
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full min-w-4xl border-collapse">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs font-medium tracking-wide text-slate-400 uppercase">
                <th className="py-2.5 pr-3 pl-4">File</th>
                <th className="px-3 py-2.5">Storage updated</th>
                <th className="px-3 py-2.5">Embedded</th>
                <th className="px-3 py-2.5 text-right">Chunks</th>
                <th className="px-3 py-2.5">State</th>
                <th className="py-2.5 pr-4 pl-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sources.map((status) => (
                <SourceRow
                  key={status.source_key}
                  status={status}
                  expanded={expanded === status.source_key}
                  busy={run.progress?.sourceKey === status.source_key}
                  actionsDisabled={run.running}
                  onToggle={() => toggle(status.source_key)}
                  onReindex={() => run.start({ keys: [status.source_key] })}
                  onDeindex={() => void handleDeindex(status.source_key)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
