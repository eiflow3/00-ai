/**
 * The sources screen: every file in storage, joined with its embeddings.
 *
 * This is the answer to "the file changed, but did its embeddings?" — the two
 * timestamps are columns of the same row, and the state column says which way
 * the comparison came out.
 *
 * Indexing is not started from here. A file can be cut several ways, each into
 * its own vector space, and choosing between them is what the Chunking screen
 * is for — so this screen owns the file (upload, replace, delete) and reports
 * where its copies live, while the decision of how to cut one lives next door.
 * A run started there still shows here, because it is the same queue.
 */

import { useCallback, useEffect, useState } from 'react'

import { IndexProgress } from './IndexProgress'
import { SourceRow } from './SourceRow'
import { UploadPanel } from './UploadPanel'
import { basename } from './uploadRules'
import { deindexSource, deleteSource, replaceSource } from '../../api/client'
import type { IndexState, SourceStatus } from '../../api/types'
import { ChoiceDialog } from '../../components/ChoiceDialog'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EmptyState } from '../../components/EmptyState'
import { Spinner } from '../../components/Spinner'
import { useIndexRun } from '../../hooks/useIndexRun'
import { useSources } from '../../hooks/useSources'
import { useUpload } from '../../hooks/useUpload'

/** The filter options, in the order they read most naturally. */
const FILTERS: { value: IndexState | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'stale_content', label: 'Changed' },
  { value: 'not_indexed', label: 'Not indexed' },
  { value: 'interrupted', label: 'Part indexed' },
  { value: 'current', label: 'Current' },
  { value: 'orphaned', label: 'Orphaned' },
]

/**
 * How often to re-read the list while work is in flight that this tab is not
 * streaming — another tab's run, or this one before it has attached.
 */
const INDEXING_POLL_MS = 3000

/** A replace waiting on the user, because the chosen file has a different name. */
interface PendingReplace {
  sourceKey: string
  file: File
}

interface SourcesViewProps {
  /** Open the Chunking bench on this file and variant. */
  onOpenVariant: (sourceKey: string, variantId: string) => void
}

export function SourcesView({ onOpenVariant }: SourcesViewProps) {
  const [filter, setFilter] = useState<IndexState | 'all'>('all')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingReplace | null>(null)
  // The row whose delete dialog is open. Held whole rather than by key, because
  // which deletions are on offer depends on what each side of the row holds.
  const [deleting, setDeleting] = useState<SourceStatus | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const { sources, loading, error, refresh, merge } = useSources(
    filter === 'all' ? undefined : filter,
  )

  // A finished run reports each file's re-read status, so the table updates
  // from the stream itself rather than issuing another list request.
  const run = useIndexRun(merge)

  // A new file is not in the table yet, so re-read rather than merging it in.
  const upload = useUpload(refresh)

  // Poll only when work is in flight that this tab is *not* streaming. Holding
  // the stream already delivers every change the poll would find, so polling
  // alongside it asks a question that is already being answered.
  const watching =
    !run.running && sources.some((status) => status.indexing || status.queued)

  useEffect(() => {
    if (!watching) return

    const timer = setInterval(refresh, INDEXING_POLL_MS)
    return () => clearInterval(timer)
  }, [watching, refresh])

  const toggle = useCallback((sourceKey: string) => {
    setExpanded((current) => (current === sourceKey ? null : sourceKey))
  }, [])

  /**
   * Run one of the two deletions and show what the table looks like after.
   *
   * Both re-read the list rather than merging a row: one of them removes the
   * row entirely, and the other changes which side of the join knows the key.
   */
  const performDelete = useCallback(
    async (sourceKey: string, vectorsOnly: boolean) => {
      setActionError(null)
      try {
        if (vectorsOnly) {
          await deindexSource(sourceKey)
        } else {
          await deleteSource(sourceKey)
        }
        refresh()
      } catch (cause: unknown) {
        setActionError(cause instanceof Error ? cause.message : String(cause))
      }
    },
    [refresh],
  )

  /** Send the replacement, then show the file's new state. */
  const performReplace = useCallback(
    async (sourceKey: string, file: File) => {
      setActionError(null)
      try {
        const response = await replaceSource(sourceKey, file)
        // Replacing discards the old vectors, so the row's state changes on
        // both sides — take it from the response rather than re-listing.
        merge([response.status])
      } catch (cause: unknown) {
        setActionError(cause instanceof Error ? cause.message : String(cause))
      }
    },
    [merge],
  )

  /**
   * Replacing a row with a differently-named file is usually a slip and
   * occasionally deliberate, so ask rather than refuse.
   */
  const handleReplace = useCallback(
    (sourceKey: string, file: File) => {
      if (file.name === basename(sourceKey)) {
        void performReplace(sourceKey, file)
        return
      }
      setPending({ sourceKey, file })
    },
    [performReplace],
  )

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Sources</h1>
          <p className="mt-1 text-sm text-slate-500">
            Files in object storage, next to every vector space holding a copy.
            Cut and index them on the Chunking screen.
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

      <UploadPanel
        items={upload.items}
        uploading={upload.uploading}
        onUpload={(files) => void upload.upload(files)}
        onDismiss={upload.reset}
      />

      <IndexProgress
        running={run.running}
        progress={run.progress}
        queued={run.queued}
        pending={run.pending}
        failures={run.failures}
        screenings={run.screenings}
        summary={run.summary}
        reused={run.reused}
        rejected={run.rejected}
        limit={run.limit}
        error={run.error}
        resumed={run.resumed}
        onStop={() => void run.stop()}
        onDismiss={run.reset}
      />

      {actionError ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {actionError}
        </p>
      ) : null}

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
              ? 'Upload a .txt, .md or .pdf file to the bucket, then refresh.'
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
                <th className="px-3 py-2.5">Indexed in</th>
                <th className="py-2.5 pr-4 pl-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sources.map((status) => (
                <SourceRow
                  key={status.source_key}
                  status={status}
                  expanded={expanded === status.source_key}
                  // Either this session is on that file, or the server says
                  // a run is — both mean "leave it alone".
                  busy={run.progress?.sourceKey === status.source_key || status.indexing}
                  queued={status.queued}
                  // A run in flight can be joined, so a row is only withheld
                  // when the pipeline is actually holding that file.
                  actionsDisabled={false}
                  onToggle={() => toggle(status.source_key)}
                  onOpenVariant={(variantId) =>
                    onOpenVariant(status.source_key, variantId)
                  }
                  onDelete={() => setDeleting(status)}
                  onReplace={(file) => handleReplace(status.source_key, file)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ChoiceDialog
        open={deleting !== null}
        title="Delete this source?"
        onCancel={() => setDeleting(null)}
        choices={[
          {
            label: 'Delete the file and its embeddings',
            description:
              deleting?.source === null
                ? 'No file left in storage — only its vectors remain.'
                : 'Removes the file from object storage as well. Nothing is left to re-index.',
            danger: true,
            disabled: deleting?.source === null,
            onSelect: () => {
              if (deleting) void performDelete(deleting.source_key, false)
              setDeleting(null)
            },
          },
          {
            label: 'Delete the embeddings only',
            description: deleting?.indexed
              ? `Removes ${deleting.indexed.chunk_count} chunk(s) from the index. The file stays, and indexing it again restores them.`
              : 'Nothing is indexed under this key.',
            disabled: !deleting?.indexed,
            onSelect: () => {
              if (deleting) void performDelete(deleting.source_key, true)
              setDeleting(null)
            },
          },
        ]}
      >
        <p>
          <span className="font-mono">{deleting?.source_key}</span>
        </p>
        <p className="mt-2">
          Deleting the file cannot be undone — the bytes are gone from object
          storage, not moved aside.
        </p>
      </ChoiceDialog>

      <ConfirmDialog
        open={pending !== null}
        title="The file names do not match"
        confirmLabel="Replace anyway"
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (pending) void performReplace(pending.sourceKey, pending.file)
          setPending(null)
        }}
      >
        <p>
          You picked <span className="font-mono">{pending?.file.name}</span>, but this row
          is <span className="font-mono">{basename(pending?.sourceKey ?? '')}</span>.
        </p>
        <p className="mt-2">
          Continuing stores the new contents under{' '}
          <span className="font-mono">{pending?.sourceKey}</span> and discards every chunk
          embedded from the old version.
        </p>
      </ConfirmDialog>
    </div>
  )
}
