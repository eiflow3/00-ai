/**
 * The chunks one file currently occupies in the vector index.
 *
 * Fetched on expand rather than with the list: a file's chunks are only
 * interesting once someone asks for them, and fetching every file's would be
 * one request per row.
 */

import { useEffect, useState } from 'react'

import { getSource } from '../../api/client'
import type { SourceChunk } from '../../api/types'
import { EmptyState } from '../../components/EmptyState'
import { Spinner } from '../../components/Spinner'

interface ChunkListProps {
  sourceKey: string
}

/** What the last settled fetch produced, tagged with the file it was for. */
interface Result {
  sourceKey: string
  chunks: SourceChunk[]
  error: string | null
}

const PENDING: Result = { sourceKey: '', chunks: [], error: null }

export function ChunkList({ sourceKey }: ChunkListProps) {
  const [result, setResult] = useState<Result>(PENDING)

  useEffect(() => {
    const abort = new AbortController()

    getSource(sourceKey, abort.signal)
      .then((detail) => {
        if (!abort.signal.aborted) {
          setResult({ sourceKey, chunks: detail.chunks, error: null })
        }
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setResult({
          sourceKey,
          chunks: [],
          error: cause instanceof Error ? cause.message : String(cause),
        })
      })

    return () => abort.abort()
  }, [sourceKey])

  // Loading whenever the settled result belongs to a different file, which
  // keeps it derived rather than set synchronously inside the effect.
  const loading = result.sourceKey !== sourceKey
  const { chunks, error } = result

  if (!loading && error) {
    return <p className="px-4 py-3 text-sm text-state-orphaned">{error}</p>
  }

  if (loading) {
    return (
      <p className="flex items-center gap-2 px-4 py-3 text-sm text-slate-400">
        <Spinner />
        Loading chunks…
      </p>
    )
  }

  if (chunks.length === 0) {
    return (
      <div className="px-4 py-3">
        <EmptyState
          title="No chunks in the index"
          hint="Nothing has been embedded from this file yet."
        />
      </div>
    )
  }

  return (
    <ol className="divide-y divide-slate-100">
      {chunks.map((chunk) => (
        <li key={chunk.vector_id} className="flex gap-4 px-4 py-3">
          <div className="w-40 shrink-0">
            {/* The vector id is the link back to Pinecone — worth showing verbatim. */}
            <p className="tabular font-mono text-xs text-slate-400">{chunk.vector_id}</p>
            <p className="mt-0.5 text-xs text-slate-400">{chunk.char_count} chars</p>
          </div>
          <p className="min-w-0 flex-1 text-sm whitespace-pre-wrap text-slate-600">
            {chunk.content}
          </p>
        </li>
      ))}
    </ol>
  )
}
