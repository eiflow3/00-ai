/**
 * One row of the sources table: a file, its embeddings, and the verdict.
 *
 * The two timestamps sit side by side because the comparison between them is
 * the whole point of the screen. A dash in either column is meaningful — no
 * embedded date means never indexed, no storage date means the file is gone.
 */

import { StateBadge } from './StateBadge'
import { needsReindex } from './state'
import { ACCEPT_ATTRIBUTE } from './uploadRules'
import { ChunkList } from './ChunkList'
import { useRef } from 'react'
import type { SourceStatus } from '../../api/types'
import { RelativeTime } from '../../components/RelativeTime'
import { Spinner } from '../../components/Spinner'

interface SourceRowProps {
  status: SourceStatus
  expanded: boolean
  /** True while a run — this session's or another's — is embedding this file. */
  busy: boolean
  /**
   * True while the file waits its turn.
   *
   * Distinct from `busy`: one worker drains the queue, so a file is accepted
   * long before anything starts happening to it, and showing a spinner during
   * that wait would claim work that has not begun.
   */
  queued: boolean
  /** Withheld while this file cannot usefully be acted on. */
  actionsDisabled: boolean
  onToggle: () => void
  onReindex: () => void
  onDeindex: () => void
  /** Hand the chosen file up; the view decides whether to confirm first. */
  onReplace: (file: File) => void
}

/** Row tint by state, so a stale file is visible without reading the badge. */
const ROW_ACCENT: Record<string, string> = {
  stale_content: 'border-l-state-stale',
  stale_model: 'border-l-state-stale',
  interrupted: 'border-l-state-orphaned',
  orphaned: 'border-l-state-orphaned',
  current: 'border-l-state-current',
}

export function SourceRow({
  status,
  expanded,
  busy,
  queued,
  actionsDisabled,
  onToggle,
  onReindex,
  onDeindex,
  onReplace,
}: SourceRowProps) {
  const fileInput = useRef<HTMLInputElement>(null)
  const { source, indexed, state } = status
  const accent = ROW_ACCENT[state] ?? 'border-l-transparent'

  // Only a file that still exists in storage can be indexed; an orphan's
  // resolution is deleting its vectors instead.
  // A run already embedding this file makes a second request pointless: the
  // server would refuse it anyway rather than let two runs interleave.
  const canReindex = source !== null && needsReindex(state) && !busy
  const canDeindex = indexed !== null && !busy
  // An orphan has no file left to replace — only vectors to remove.
  const canReplace = source !== null && !busy

  return (
    <>
      <tr className={`border-l-2 ${accent} hover:bg-slate-50/70`}>
        <td className="py-3 pr-3 pl-4">
          <button
            type="button"
            onClick={onToggle}
            className="flex items-center gap-2 text-left"
            aria-expanded={expanded}
          >
            <span
              aria-hidden="true"
              className={`text-xs text-slate-400 transition-transform ${expanded ? 'rotate-90' : ''}`}
            >
              ▶
            </span>
            <span className="font-mono text-sm break-all text-slate-800">
              {status.source_key}
            </span>
          </button>
        </td>

        {/* The storage side. */}
        <td className="px-3 py-3 text-sm">
          <RelativeTime value={source?.last_modified} />
        </td>

        {/* The index side. */}
        <td className="px-3 py-3 text-sm">
          <RelativeTime value={indexed?.embedded_at} />
        </td>

        <td className="tabular px-3 py-3 text-right text-sm text-slate-500">
          {indexed ? indexed.chunk_count : <span className="text-slate-300">—</span>}
        </td>

        <td className="px-3 py-3">
          <StateBadge state={state} detail={status.detail} />
        </td>

        <td className="py-3 pr-4 pl-3 text-right whitespace-nowrap">
          {canReplace ? (
            <>
              <input
                ref={fileInput}
                type="file"
                accept={ACCEPT_ATTRIBUTE}
                className="hidden"
                onChange={(event) => {
                  const [file] = event.target.files ?? []
                  if (file) onReplace(file)
                  // Reset so re-picking the same file still fires a change.
                  event.target.value = ''
                }}
              />
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                disabled={actionsDisabled}
                className="mr-1.5 rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40"
              >
                Replace
              </button>
            </>
          ) : null}
          {busy ? (
            <span className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-400">
              <Spinner />
              Indexing
            </span>
          ) : queued ? (
            <span
              title="Waiting for the worker to reach it."
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-400"
            >
              Queued
            </span>
          ) : canReindex ? (
            <button
              type="button"
              onClick={onReindex}
              disabled={actionsDisabled}
              className="rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40"
            >
              Index
            </button>
          ) : null}
          {canDeindex ? (
            <button
              type="button"
              onClick={onDeindex}
              disabled={actionsDisabled}
              className="ml-1.5 rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-500 hover:bg-state-orphaned-soft hover:text-state-orphaned disabled:opacity-40"
            >
              Remove vectors
            </button>
          ) : null}
        </td>
      </tr>

      {expanded ? (
        <tr>
          <td colSpan={6} className="bg-slate-50/60 p-0">
            <div className="border-y border-slate-100">
              {/* The provenance that links the two sides, shown verbatim. */}
              {indexed ? (
                <dl className="flex flex-wrap gap-x-8 gap-y-2 px-4 py-3 text-xs">
                  <div>
                    <dt className="text-slate-400">Document id</dt>
                    <dd className="font-mono text-slate-600">{indexed.document_id}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-400">Storage hash now</dt>
                    <dd className="font-mono text-slate-600">{source?.etag ?? '—'}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-400">Hash when embedded</dt>
                    <dd className="font-mono text-slate-600">{indexed.source_etag || '—'}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-400">Embedding model</dt>
                    <dd className="font-mono text-slate-600">{indexed.embedding_model || '—'}</dd>
                  </div>
                </dl>
              ) : null}
              <ChunkList sourceKey={status.source_key} />
            </div>
          </td>
        </tr>
      ) : null}
    </>
  )
}
