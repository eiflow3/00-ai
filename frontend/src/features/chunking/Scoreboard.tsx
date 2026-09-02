/**
 * The answer to "which way of cutting this document is better".
 *
 * Reading two answers side by side tells you which one you preferred that time.
 * It does not separate four strategies that are all roughly reasonable, and on
 * a document like an annual report they usually are. So the same questions go
 * to every variant and the results are counted.
 *
 * Retrieval recall leads the table, not correctness. Whether the passage the
 * answer needed came back is chunking's job; whether the answer reads well is
 * the model's, and a capable model papers over a mediocre retrieval often
 * enough to hide a real difference.
 *
 * The grid underneath is where a number becomes a fixable problem: one row per
 * question, one column per variant, and clicking a cell shows what that variant
 * actually retrieved.
 */

import { useMemo, useState } from 'react'

import type { GoldenSet, RowScore, VariantScore } from '../../api/types'
import { EmptyState } from '../../components/EmptyState'
import { Spinner } from '../../components/Spinner'
import type { UseVariantScoreResult } from '../../hooks/useVariantScore'

interface ScoreboardProps {
  sets: GoldenSet[]
  /** Restricts the picker to sets drafted from the file being worked on. */
  sourceKey: string
  run: UseVariantScoreResult
  onScore: (setId: string, topK: number, generate: boolean) => void
}

/** Retrieved chunks per question. The same for every variant, by design. */
const DEFAULT_TOP_K = 5

/** A cell the person clicked, so the detail below knows what to show. */
interface Opened {
  variantId: string
  questionId: string
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

export function Scoreboard({ sets, sourceKey, run, onScore }: ScoreboardProps) {
  const [setId, setSetId] = useState('')
  const [topK, setTopK] = useState(DEFAULT_TOP_K)
  const [generate, setGenerate] = useState(true)
  const [opened, setOpened] = useState<Opened | null>(null)

  // A golden set is an answer key for one document; scoring variants of a
  // different file against it would measure nothing.
  const usable = sets.filter((set) => sourceKey === '' || set.source_key === sourceKey)

  // The ranking once the run has closed, or what has finished so far while it
  // is still going — so the table fills in rather than appearing at the end.
  const results: VariantScore[] = run.summary?.scores ?? run.finished

  // Questions down the side, in the order the first variant was asked them.
  const questions = useMemo(() => {
    const first = results[0]
    return first ? first.scores.map((score) => score.question_id) : []
  }, [results])

  const byVariant = useMemo(() => {
    const index: Record<string, Record<string, RowScore>> = {}
    for (const result of results) {
      index[result.variant_id] = Object.fromEntries(
        result.scores.map((score) => [score.question_id, score]),
      )
    }
    return index
  }, [results])

  const detail = opened ? byVariant[opened.variantId]?.[opened.questionId] : undefined

  return (
    <section className="mt-8">
      <h2 className="text-sm font-semibold text-slate-900">Scoreboard</h2>
      <p className="mt-1 mb-3 text-sm text-slate-500">
        Every variant is asked the same questions, with the same model and the
        same number of chunks. The only thing that differs is how the document
        was cut.
      </p>

      <div className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white px-4 py-4">
        <label className="min-w-64 flex-1">
          <span className="mb-1 block text-xs font-medium text-slate-600">Golden set</span>
          <select
            value={setId}
            onChange={(event) => setSetId(event.target.value)}
            disabled={run.running}
            className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">Choose a set…</option>
            {usable.map((set) => (
              <option key={set.set_id} value={set.set_id}>
                {set.slug} — {set.row_count} questions
              </option>
            ))}
          </select>
        </label>

        <label>
          <span className="mb-1 block text-xs font-medium text-slate-600">Chunks each</span>
          <input
            type="number"
            min={1}
            max={50}
            value={topK}
            onChange={(event) => setTopK(Number(event.target.value))}
            disabled={run.running}
            className="tabular w-24 rounded border border-slate-300 px-2 py-1.5 text-sm"
          />
        </label>

        <label className="flex items-center gap-2 pb-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={generate}
            onChange={(event) => setGenerate(event.target.checked)}
            disabled={run.running}
          />
          Answer the questions too
        </label>

        {run.running ? (
          <button
            type="button"
            onClick={() => void run.stop()}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100"
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onScore(setId, topK, generate)}
            disabled={setId === ''}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-30"
          >
            Score every variant
          </button>
        )}
      </div>

      {!generate ? (
        <p className="mb-4 text-xs text-slate-400">
          Retrieval only — no model calls, so it costs nothing but a few
          embeddings. It still answers which chunking found the right passage.
        </p>
      ) : null}

      {run.error ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {run.error}
        </p>
      ) : null}

      {run.failures.map((message, position) => (
        <p
          key={`${message}-${position}`}
          className="mb-2 rounded-lg border border-state-stale-soft bg-state-stale-soft px-4 py-2 text-sm text-state-stale"
        >
          {message}
        </p>
      ))}

      {run.running ? (
        <p className="mb-4 flex items-center gap-2 text-sm text-slate-500">
          <Spinner />
          {run.completed} of {run.total} questions answered
          {run.started ? ` across ${run.started.variants.length} variants` : ''}…
        </p>
      ) : null}

      {results.length === 0 && !run.running ? (
        <EmptyState
          title="No comparison run yet"
          hint="Pick a golden set and score every variant. Two variants is the fewest that tells you anything."
        />
      ) : null}

      {results.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full min-w-3xl border-collapse">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs font-medium tracking-wide text-slate-400 uppercase">
                <th className="py-2.5 pr-3 pl-4">Variant</th>
                <th className="px-3 py-2.5 text-right">Found the passage</th>
                <th className="px-3 py-2.5 text-right">Answered right</th>
                <th className="px-3 py-2.5 text-right">Precision</th>
                <th className="px-3 py-2.5 text-right">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {results.map((result) => (
                <tr
                  key={result.variant_id}
                  className={`text-sm ${
                    run.summary?.winner === result.variant_id ? 'bg-state-current-soft' : ''
                  }`}
                >
                  <td className="py-2.5 pr-3 pl-4 font-medium text-slate-900">
                    {result.label}
                    {run.summary?.winner === result.variant_id ? (
                      <span className="ml-2 text-xs font-normal text-state-current">
                        best
                      </span>
                    ) : null}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-slate-800">
                    {percent(result.recall)}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-slate-600">
                    {result.scores.some((score) => score.correct !== null)
                      ? `${result.correct} / ${result.rows}`
                      : '—'}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-slate-600">
                    {percent(result.precision)}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right text-slate-400">
                    {result.duration_seconds.toFixed(0)}s
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {questions.length > 0 && results.length > 1 ? (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs font-medium tracking-wide text-slate-400 uppercase">
                <th className="py-2.5 pr-3 pl-4 text-left">Question</th>
                {results.map((result) => (
                  <th key={result.variant_id} className="px-3 py-2.5 text-center">
                    {result.config?.strategy ?? result.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {questions.map((questionId) => (
                <tr key={questionId}>
                  <td className="py-2 pr-3 pl-4 text-xs text-slate-500">
                    <span className="tabular font-mono">{questionId}</span>{' '}
                    {byVariant[results[0].variant_id]?.[questionId]?.question}
                  </td>
                  {results.map((result) => {
                    const score = byVariant[result.variant_id]?.[questionId]

                    // Three outcomes, not two. Recall is the mark — whether
                    // the passage the answer needed came back, which is what
                    // chunking decides. But a question the document cannot
                    // answer cites no section, so there is nothing to retrieve
                    // and nothing to get wrong: marking those as misses would
                    // show every variant failing at something none of them
                    // could have done.
                    const measured = score?.recall ?? null
                    const mark = measured === null ? '–' : measured >= 1 ? '✓' : '✗'
                    const tone =
                      measured === null
                        ? 'text-slate-300'
                        : measured >= 1
                          ? 'text-state-current'
                          : 'text-state-orphaned'

                    return (
                      <td key={result.variant_id} className="px-3 py-2 text-center">
                        <button
                          type="button"
                          onClick={() =>
                            setOpened({
                              variantId: result.variant_id,
                              questionId,
                            })
                          }
                          title={
                            measured === null
                              ? 'Nothing to retrieve — the document does not answer this'
                              : 'See what this variant retrieved'
                          }
                          className={`${tone} hover:underline`}
                        >
                          {mark}
                        </button>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {detail ? (
        <article className="mt-4 rounded-lg border border-slate-200 bg-white px-4 py-4">
          <div className="flex items-start justify-between gap-4">
            <h3 className="text-sm font-medium text-slate-900">{detail.question}</h3>
            <button
              type="button"
              onClick={() => setOpened(null)}
              className="text-xs text-slate-400 hover:text-slate-600"
            >
              Close
            </button>
          </div>

          <dl className="mt-3 space-y-2 text-sm">
            <div>
              <dt className="text-xs font-medium text-slate-500">Should have come from</dt>
              <dd className="text-slate-700">
                {detail.gold_sections.join(', ') ||
                  'nowhere — the document does not answer this, so retrieval is not scored'}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-slate-500">Actually retrieved</dt>
              <dd className="text-slate-700">
                {detail.retrieved_sections.join(', ') || 'nothing that maps to a section'}
              </dd>
            </div>
            {detail.answer ? (
              <div>
                <dt className="text-xs font-medium text-slate-500">Answered</dt>
                <dd className="whitespace-pre-wrap text-slate-700">{detail.answer}</dd>
              </div>
            ) : null}
            {detail.reasons.length > 0 ? (
              <div>
                <dt className="text-xs font-medium text-slate-500">Marked wrong because</dt>
                <dd className="text-state-orphaned">{detail.reasons.join('; ')}</dd>
              </div>
            ) : null}
            {detail.error ? (
              <div>
                <dt className="text-xs font-medium text-slate-500">Failed</dt>
                <dd className="text-state-orphaned">{detail.error}</dd>
              </div>
            ) : null}
          </dl>

          <p className="tabular mt-3 text-xs text-slate-400">
            best match scored {detail.top_score.toFixed(3)}
          </p>
        </article>
      ) : null}
    </section>
  )
}
