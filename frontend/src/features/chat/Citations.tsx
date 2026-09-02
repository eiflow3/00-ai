/**
 * The chunks that grounded an answer, each with its similarity score.
 *
 * These arrive as a single event before any answer text, so they render while
 * the answer is still streaming — which is the point of sending them first.
 */

import type { RetrievedChunk } from '../../api/types'
import { AnswerText } from '../../components/AnswerText'

interface CitationsProps {
  chunks: RetrievedChunk[]
  /** True once retrieval has reported, so an empty list means "found nothing". */
  retrieved: boolean
}

/** Tint a score by strength, so a weak match is not read as a confident one. */
function scoreClass(score: number): string {
  if (score >= 0.75) return 'text-state-current'
  if (score >= 0.5) return 'text-state-stale'
  return 'text-slate-400'
}

export function Citations({ chunks, retrieved }: CitationsProps) {
  if (!retrieved) return null

  if (chunks.length === 0) {
    return (
      <p className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500">
        Nothing in the index matched this question — the answer below is ungrounded.
      </p>
    )
  }

  return (
    <section className="mb-5">
      <h2 className="mb-2 text-xs font-medium tracking-wide text-slate-400 uppercase">
        Grounded in {chunks.length} chunk{chunks.length === 1 ? '' : 's'}
      </h2>

      <ul className="space-y-2">
        {chunks.map((chunk) => (
          <li
            key={chunk.chunk_id}
            className="rounded-lg border border-slate-200 bg-white px-4 py-3"
          >
            <div className="mb-1.5 flex items-baseline justify-between gap-3">
              {/* The source key traces the citation straight back to the file. */}
              <span className="font-mono text-xs break-all text-slate-500">
                {chunk.source || chunk.chunk_id}
              </span>
              <span className={`tabular text-xs font-medium ${scoreClass(chunk.score)}`}>
                {chunk.score.toFixed(3)}
              </span>
            </div>
            <p className="line-clamp-3 text-sm whitespace-pre-wrap text-slate-600">
              {/* Chunk text can carry a table:// link where a table used to be. */}
              <AnswerText text={chunk.content} />
            </p>
          </li>
        ))}
      </ul>
    </section>
  )
}
