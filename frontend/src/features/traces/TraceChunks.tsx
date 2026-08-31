/**
 * The chunks one answer was built from — the evidence half of an evaluation.
 *
 * This is what makes "retrieval or generation?" answerable. If the passage that
 * answers the question is sitting here and the answer still got it wrong, the
 * retrieval did its job. If it is absent, no amount of prompting would have
 * helped.
 *
 * Chunks that the score threshold dropped are shown too, marked, because a
 * near-miss is the third possible answer: neither stage failed, the threshold
 * was simply set too high.
 */

import type { TraceChunk } from '../../api/types'

interface TraceChunksProps {
  chunks: TraceChunk[]
  /** The threshold in force, so a dropped chunk's score can be read against it. */
  scoreThreshold: number
}

/** Tint a score by strength, matching how citations are shown on the chat screen. */
function scoreClass(score: number): string {
  if (score >= 0.75) return 'text-state-current'
  if (score >= 0.5) return 'text-state-stale'
  return 'text-slate-400'
}

export function TraceChunks({ chunks, scoreThreshold }: TraceChunksProps) {
  if (chunks.length === 0) {
    return (
      <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-500">
        Nothing was retrieved for this question — the answer was ungrounded.
      </p>
    )
  }

  const used = chunks.filter((chunk) => !chunk.dropped)
  const dropped = chunks.filter((chunk) => chunk.dropped)

  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-400">
        {used.length} chunk{used.length === 1 ? '' : 's'} reached the prompt
        {dropped.length > 0
          ? `, ${dropped.length} dropped below the ${scoreThreshold.toFixed(2)} threshold`
          : ''}
      </p>

      {chunks.map((chunk) => (
        <article
          key={`${chunk.rank}-${chunk.chunk_id}`}
          className={`rounded-lg border px-3 py-2 ${
            chunk.dropped
              ? 'border-dashed border-slate-200 bg-slate-50'
              : 'border-slate-200 bg-white'
          }`}
        >
          <div className="mb-1 flex items-baseline justify-between gap-3">
            <span className="font-mono text-xs break-all text-slate-500">
              #{chunk.rank + 1} {chunk.source_key || chunk.chunk_id}
            </span>
            <span className="flex shrink-0 items-center gap-2">
              {chunk.dropped ? (
                <span className="rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-500">
                  dropped
                </span>
              ) : null}
              <span className={`tabular text-xs font-medium ${scoreClass(chunk.score)}`}>
                {chunk.score.toFixed(3)}
              </span>
            </span>
          </div>

          {/* Full text, never truncated — a summarised chunk cannot settle
              whether the answer was in front of the model. */}
          <p className="text-sm whitespace-pre-wrap text-slate-600">{chunk.content}</p>

          <p className="mt-1 font-mono text-xs text-slate-300">
            {chunk.chunk_id} · {chunk.char_count} chars · {chunk.content_hash}
          </p>
        </article>
      ))}
    </div>
  )
}
