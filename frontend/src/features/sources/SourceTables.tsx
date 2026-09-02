/**
 * The tables extraction stored for one file, listed in its expanded row.
 *
 * Fetched on expand, like ChunkList, and rendered only when there is something
 * to show: most files have no tables, and an empty section for every .txt
 * would be noise. Each row opens the same TableDialog a chat link does.
 */

import { useEffect, useState } from 'react'

import { getTables } from '../../api/client'
import type { ExtractedTable } from '../../api/types'
import { TableDialog } from '../../components/TableDialog'
import type { TableRef } from '../../components/TableDialog'

interface SourceTablesProps {
  documentId: string
}

/** What the last settled fetch produced, tagged with the document it was for. */
interface Result {
  documentId: string
  tables: ExtractedTable[]
}

const PENDING: Result = { documentId: '', tables: [] }

export function SourceTables({ documentId }: SourceTablesProps) {
  const [result, setResult] = useState<Result>(PENDING)
  const [open, setOpen] = useState<TableRef | null>(null)

  useEffect(() => {
    const abort = new AbortController()

    getTables(documentId, abort.signal)
      .then((response) => {
        if (!abort.signal.aborted) {
          setResult({ documentId, tables: response.tables })
        }
      })
      // No tables — or no extraction at all — renders as nothing either way.
      .catch(() => {
        if (!abort.signal.aborted) setResult({ documentId, tables: [] })
      })

    return () => abort.abort()
  }, [documentId])

  if (result.documentId !== documentId || result.tables.length === 0) return null

  return (
    <div className="border-t border-slate-100 px-4 py-3">
      <h3 className="mb-2 text-xs font-medium tracking-wide text-slate-400 uppercase">
        Extracted tables
      </h3>
      <ul className="flex flex-wrap gap-2">
        {result.tables.map((table) => (
          <li key={table.table_id}>
            <button
              type="button"
              onClick={() => setOpen({ documentId, tableId: table.table_id })}
              className="inline-flex items-baseline gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
            >
              <span aria-hidden>▦</span>
              {table.caption || table.table_id}
              {table.page ? <span className="text-slate-400">p. {table.page}</span> : null}
            </button>
          </li>
        ))}
      </ul>
      <TableDialog table={open} onClose={() => setOpen(null)} />
    </div>
  )
}
