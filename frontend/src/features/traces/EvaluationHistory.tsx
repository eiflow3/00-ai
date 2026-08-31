/**
 * Every judgement made on one request, withdrawn ones included.
 *
 * A withdrawn verdict is kept and shown struck through rather than removed: the
 * request it points at is still evidence, and the fact that you changed your
 * mind about it is often the most interesting thing on the row.
 */

import { restoreEvaluation, withdrawEvaluation } from '../../api/client'
import type { Evaluation } from '../../api/types'
import { RelativeTime } from '../../components/RelativeTime'
import { TARGET_LABEL, VerdictBadge } from '../chat/VerdictBadge'

interface EvaluationHistoryProps {
  evaluations: Evaluation[]
  /** Look up a reason chip's label from its stored id. */
  labelFor: (tagId: string) => string
  /** Called after a withdrawal or restore, so the row's rollup can be re-read. */
  onChanged: () => void
}

export function EvaluationHistory({
  evaluations,
  labelFor,
  onChanged,
}: EvaluationHistoryProps) {
  if (evaluations.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        Not judged yet — ask this question again on the Chat tab to evaluate it.
      </p>
    )
  }

  async function withdraw(evaluation: Evaluation) {
    await withdrawEvaluation(evaluation.id)
    onChanged()
  }

  async function restore(evaluation: Evaluation) {
    await restoreEvaluation(evaluation.id)
    onChanged()
  }

  return (
    <ul className="space-y-2">
      {evaluations.map((evaluation) => (
        <li
          key={evaluation.id}
          className={`rounded-lg border px-3 py-2 ${
            evaluation.deleted
              ? 'border-dashed border-slate-200 bg-slate-50'
              : 'border-slate-200 bg-white'
          }`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <VerdictBadge
              target={evaluation.target}
              verdict={evaluation.verdict}
              muted={evaluation.deleted}
            />

            {evaluation.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-slate-200 px-2 py-0.5 text-xs text-slate-500"
              >
                {labelFor(tag)}
              </span>
            ))}

            <span className="ml-auto flex shrink-0 items-center gap-3">
              {evaluation.author !== 'human' ? (
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
                  {evaluation.author}
                </span>
              ) : null}
              <span className="text-xs">
                <RelativeTime value={evaluation.created_at} />
              </span>
              {evaluation.deleted ? (
                <button
                  type="button"
                  onClick={() => void restore(evaluation)}
                  className="text-xs font-medium text-slate-500 hover:text-slate-800"
                >
                  Restore
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void withdraw(evaluation)}
                  className="text-xs font-medium text-slate-400 hover:text-state-orphaned"
                  title="Keeps the record, but stops it counting"
                >
                  Withdraw
                </button>
              )}
            </span>
          </div>

          {evaluation.note ? (
            <p className="mt-1.5 text-sm text-slate-600">{evaluation.note}</p>
          ) : null}

          {evaluation.deleted ? (
            <p className="mt-1 text-xs text-slate-400">
              Withdrawn
              {evaluation.deleted_reason ? ` — ${evaluation.deleted_reason}` : ''}. Still
              on the record; no longer counted on {TARGET_LABEL[evaluation.target]}.
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  )
}
