/**
 * The stored table behind a table:// link, shown in place.
 *
 * Same native <dialog> mechanics as ChoiceDialog — showModal for the focus
 * trap, Escape and backdrop for free — but wider, because this one shows
 * content rather than asking for a decision. The table is fetched when the
 * dialog opens: a link may never be clicked, and the artifact behind it never
 * changes, so there is nothing to prefetch or cache.
 */

import { useEffect, useRef, useState } from 'react'

import { getTable } from '../api/client'
import type { TableArtifact } from '../api/types'
import { MarkdownTable } from './MarkdownTable'
import { Spinner } from './Spinner'

export interface TableRef {
  documentId: string
  tableId: string
}

interface TableDialogProps {
  /** Which table to show; null keeps the dialog closed. */
  table: TableRef | null
  onClose: () => void
}

/** What the last settled fetch produced, tagged with the table it was for. */
interface Result {
  key: string
  artifact: TableArtifact | null
  error: string | null
}

const PENDING: Result = { key: '', artifact: null, error: null }

export function TableDialog({ table, onClose }: TableDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [result, setResult] = useState<Result>(PENDING)

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (table && !element.open) element.showModal()
    if (!table && element.open) element.close()
  }, [table])

  useEffect(() => {
    if (!table) return
    const abort = new AbortController()
    const key = `${table.documentId}/${table.tableId}`

    getTable(table.documentId, table.tableId, abort.signal)
      .then((artifact) => {
        if (!abort.signal.aborted) setResult({ key, artifact, error: null })
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setResult({
          key,
          artifact: null,
          error: cause instanceof Error ? cause.message : String(cause),
        })
      })

    return () => abort.abort()
  }, [table])

  const key = table ? `${table.documentId}/${table.tableId}` : ''
  const loading = result.key !== key
  const { artifact, error } = result

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === dialog.current) onClose()
      }}
      className="m-auto w-full max-w-3xl rounded-lg border border-slate-200 p-0 backdrop:bg-slate-900/40"
    >
      {table ? (
        <div className="p-5">
          <div className="mb-3 flex items-baseline justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-900">
              {artifact?.caption || 'Extracted table'}
            </h2>
            <span className="shrink-0 font-mono text-xs text-slate-400">
              {table.tableId}
              {artifact?.page ? ` · p. ${artifact.page}` : ''}
            </span>
          </div>

          {loading && !error ? (
            <p className="flex items-center gap-2 py-6 text-sm text-slate-400">
              <Spinner />
              Loading table…
            </p>
          ) : error ? (
            <p className="py-4 text-sm text-state-orphaned">{error}</p>
          ) : artifact ? (
            <MarkdownTable markdown={artifact.markdown} />
          ) : null}

          <div className="mt-4 flex justify-end">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
            >
              Close
            </button>
          </div>
        </div>
      ) : null}
    </dialog>
  )
}
