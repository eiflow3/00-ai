/**
 * The evaluation screen: every question the system has been asked, and what you
 * made of the answer.
 *
 * It exists so the judgement and the evidence sit in one place. A verdict with
 * no chunks beside it cannot tell you whether to fix the index or the prompt,
 * which is the only reason to record a verdict at all.
 */

import { useState } from 'react'

import { TraceRow } from './TraceRow'
import { deleteTrace, evaluationExportUrl } from '../../api/client'
import type { Trace, Verdict } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EmptyState } from '../../components/EmptyState'
import { Spinner } from '../../components/Spinner'
import { useEvaluationOptions } from '../../hooks/useEvaluationOptions'
import { useTraces } from '../../hooks/useTraces'

/** The judged/unjudged split, as a single control. */
type Scope = 'all' | 'judged' | 'unjudged'

const SCOPES: { value: Scope; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'judged', label: 'Judged' },
  { value: 'unjudged', label: 'Not judged' },
]

const VERDICTS: { value: Verdict | ''; label: string }[] = [
  { value: '', label: 'Any verdict' },
  { value: 'good', label: 'Good' },
  { value: 'partial', label: 'Partial' },
  { value: 'bad', label: 'Bad' },
]

/** Translate the scope control into the filter the backend understands. */
function evaluatedFor(scope: Scope): boolean | undefined {
  if (scope === 'judged') return true
  if (scope === 'unjudged') return false
  return undefined
}

export function TracesView() {
  const [scope, setScope] = useState<Scope>('all')
  const [verdict, setVerdict] = useState<Verdict | ''>('')
  const [search, setSearch] = useState('')
  const [pendingDelete, setPendingDelete] = useState<Trace | null>(null)

  const catalog = useEvaluationOptions()
  const traces = useTraces({
    evaluated: evaluatedFor(scope),
    verdict: verdict || undefined,
    search: search.trim() || undefined,
  })

  async function confirmDelete() {
    const target = pendingDelete
    setPendingDelete(null)
    if (!target) return

    await deleteTrace(target.trace_id)
    traces.forget(target.trace_id)
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Evaluations</h1>
          <p className="mt-1 text-sm text-slate-500">
            Every question asked, the chunks it retrieved, and what you made of the
            answer.
          </p>
        </div>

        <a
          href={evaluationExportUrl()}
          download
          className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
        >
          Download JSONL
        </a>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-slate-200">
          {SCOPES.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setScope(option.value)}
              aria-pressed={scope === option.value}
              className={`px-3 py-1.5 text-sm font-medium first:rounded-l-md last:rounded-r-md ${
                scope === option.value
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        <select
          value={verdict}
          onChange={(event) => setVerdict(event.target.value as Verdict | '')}
          className="rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-700"
        >
          {VERDICTS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>

        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search questions…"
          className="min-w-0 flex-1 rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-800 outline-none placeholder:text-slate-400 focus:border-slate-400"
        />

        <button
          type="button"
          onClick={traces.refresh}
          className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
        >
          Refresh
        </button>
      </div>

      {traces.error ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {traces.error}
        </p>
      ) : null}

      {traces.loading ? (
        <p className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner />
          Loading…
        </p>
      ) : null}

      {!traces.loading && traces.traces.length === 0 && !traces.error ? (
        <EmptyState
          title={scope === 'judged' ? 'Nothing judged yet' : 'No questions recorded'}
          hint={
            scope === 'judged'
              ? 'Ask something on the Chat tab, then evaluate the answer.'
              : 'Every chat request is recorded here once it has been asked.'
          }
        />
      ) : null}

      {traces.traces.length > 0 ? (
        <>
          <p className="mb-2 text-xs text-slate-400">
            {traces.total} request{traces.total === 1 ? '' : 's'}
            {traces.traces.length < traces.total
              ? ` — showing the ${traces.traces.length} most recent`
              : ''}
          </p>

          <ul className="space-y-2">
            {traces.traces.map((trace) => (
              <TraceRow
                key={trace.trace_id}
                trace={trace}
                labelFor={catalog.labelFor}
                onChanged={traces.refresh}
                onDelete={setPendingDelete}
              />
            ))}
          </ul>
        </>
      ) : null}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Discard this trace?"
        confirmLabel="Discard"
        onConfirm={() => void confirmDelete()}
        onCancel={() => setPendingDelete(null)}
      >
        <p>
          This removes the question, the answer, the chunks it retrieved and every
          judgement made on it. Unlike withdrawing a verdict, nothing is kept.
        </p>
        {pendingDelete ? (
          <p className="mt-2 font-medium text-slate-800">{pendingDelete.question}</p>
        ) : null}
      </ConfirmDialog>
    </div>
  )
}
