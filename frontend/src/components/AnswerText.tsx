/**
 * Streamed text with table:// links made clickable.
 *
 * There is no markdown renderer in this app — answers are shown as the plain
 * text the stream sent. The one exception is the link the pipeline itself
 * writes when it stores a table: `[label](table://{document_id}/{table_id})`,
 * spelled by provenance.py and recognised here by exactly that shape. Each
 * match becomes a chip that opens the stored table in a dialog; everything
 * else stays verbatim text, so there is nothing to escape and nothing to
 * mis-parse.
 */

import { useState } from 'react'

import { TableDialog } from './TableDialog'
import type { TableRef } from './TableDialog'

// The exact link shape provenance.py emits: 16-hex document id, zero-padded
// table id. Anchored to that, so ordinary bracketed text never matches.
const TABLE_LINK = /\[([^\]]+)\]\(table:\/\/([0-9a-f]{16})\/(table-\d{3})\)/g

interface AnswerTextProps {
  text: string
}

export function AnswerText({ text }: AnswerTextProps) {
  const [open, setOpen] = useState<TableRef | null>(null)

  const parts: React.ReactNode[] = []
  let cursor = 0

  for (const match of text.matchAll(TABLE_LINK)) {
    const [whole, label, documentId, tableId] = match
    if (match.index > cursor) parts.push(text.slice(cursor, match.index))

    parts.push(
      <button
        key={`${documentId}/${tableId}/${match.index}`}
        type="button"
        onClick={() => setOpen({ documentId, tableId })}
        className="inline-flex items-baseline gap-1 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 align-baseline text-xs font-medium text-slate-700 hover:bg-slate-100"
        title={`Show ${tableId}`}
      >
        <span aria-hidden>▦</span>
        {label}
      </button>,
    )
    cursor = match.index + whole.length
  }

  if (cursor < text.length) parts.push(text.slice(cursor))

  return (
    <>
      {parts}
      <TableDialog table={open} onClose={() => setOpen(null)} />
    </>
  )
}
