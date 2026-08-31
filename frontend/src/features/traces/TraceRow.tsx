/**
 * One recorded request in the list, expandable into its full evidence.
 *
 * The detail is fetched only when the row is opened: a trace carries every
 * chunk's full text, which is exactly what makes it useful and exactly what
 * makes loading fifty of them at once a bad idea.
 */

import { useState } from 'react'

import { EvaluationHistory } from './EvaluationHistory'
import { TraceChunks } from './TraceChunks'
import { getTrace } from '../../api/client'
import type { EvaluationTarget, Trace, TraceDetail, Verdict } from '../../api/types'
import { RelativeTime } from '../../components/RelativeTime'
import { Spinner } from '../../components/Spinner'
import { VerdictBadge } from '../chat/VerdictBadge'

interface TraceRowProps {
  trace: Trace
  labelFor: (tagId: string) => string
  /** Called when a judgement changed, so the list's rollup can be re-read. */
  onChanged: () => void
  onDelete: (trace: Trace) => void
}

/** Order the badges consistently, whichever stages happen to be judged. */
const TARGET_ORDER: EvaluationTarget[] = ['overall', 'retrieval', 'generation']

export function TraceRow({ trace, labelFor, onChanged, onDelete }: TraceRowProps) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      setDetail(await getTrace(trace.trace_id))
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }

  function toggle() {
    const next = !open
    setOpen(next)
    if (next && detail === null) void load()
  }

  return (
    <li className="rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-slate-50"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-slate-800">
            {trace.question}
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <RelativeTime value={trace.created_at} />
            <span>·</span>
            <span>{trace.model || 'no model'}</span>
            <span>·</span>
            <span className="tabular">
              {trace.chunk_count} chunk{trace.chunk_count === 1 ? '' : 's'}
            </span>
            {trace.chunk_count > 0 ? (
              <>
                <span>·</span>
                <span className="tabular">top {trace.top_score.toFixed(2)}</span>
              </>
            ) : null}
            <span>·</span>
            <span className="tabular">${trace.total_cost.toFixed(5)}</span>
            {trace.state !== 'completed' ? (
              <span className="rounded bg-state-stale-soft px-1.5 py-0.5 text-state-stale">
                {trace.state}
              </span>
            ) : null}
          </span>
        </span>

        <span className="flex shrink-0 flex-wrap items-center justify-end gap-1">
          {TARGET_ORDER.filter((target) => trace.verdicts[target]).map((target) => (
            <VerdictBadge
              key={target}
              target={target}
              verdict={trace.verdicts[target] as Verdict}
            />
          ))}
          {trace.evaluation_count === 0 ? (
            <span className="text-xs text-slate-300">unjudged</span>
          ) : null}
        </span>
      </button>

      {open ? (
        <div className="border-t border-slate-100 px-4 py-4">
          {loading ? (
            <p className="flex items-center gap-2 text-sm text-slate-400">
              <Spinner />
              Loading the evidence…
            </p>
          ) : null}

          {error ? <p className="text-sm text-state-orphaned">{error}</p> : null}

          {detail ? (
            <div className="space-y-5">
              <section>
                <h3 className="mb-1.5 text-xs font-medium tracking-wide text-slate-400 uppercase">
                  Answer
                </h3>
                {detail.trace.answer ? (
                  <p className="text-sm leading-relaxed whitespace-pre-wrap text-slate-700">
                    {detail.trace.answer}
                  </p>
                ) : (
                  <p className="text-sm text-slate-400">
                    No answer was produced
                    {detail.trace.error_message ? ` — ${detail.trace.error_message}` : '.'}
                  </p>
                )}
              </section>

              <section>
                <h3 className="mb-1.5 text-xs font-medium tracking-wide text-slate-400 uppercase">
                  Retrieved chunks
                </h3>
                <TraceChunks
                  chunks={detail.chunks}
                  scoreThreshold={detail.trace.score_threshold}
                />
              </section>

              <section>
                <h3 className="mb-1.5 text-xs font-medium tracking-wide text-slate-400 uppercase">
                  Judgements
                </h3>
                <EvaluationHistory
                  evaluations={detail.evaluations}
                  labelFor={labelFor}
                  onChanged={() => {
                    void load()
                    onChanged()
                  }}
                />
              </section>

              <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-slate-100 pt-3 text-xs text-slate-400">
                <span className="font-mono">{detail.trace.trace_id}</span>
                <span className="tabular">
                  {detail.trace.retrieval_ms}ms retrieval · {detail.trace.generation_ms}ms
                  generation
                </span>
                <span className="tabular">
                  {detail.trace.input_tokens} in / {detail.trace.output_tokens} out
                </span>
                <span>
                  top_k {detail.trace.top_k} · threshold{' '}
                  {detail.trace.score_threshold.toFixed(2)} ·{' '}
                  {detail.trace.embedding_model || 'no embedding model'}
                </span>
                <button
                  type="button"
                  onClick={() => onDelete(detail.trace)}
                  className="ml-auto font-medium text-slate-400 hover:text-state-orphaned"
                >
                  Discard this trace
                </button>
              </footer>
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  )
}
