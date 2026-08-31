/**
 * The Golden Sets screen: generate an answer key from a document, then judge it.
 *
 * A golden set is what every future eval score is measured against, so a wrong
 * answer key silently marks correct answers wrong from then on and nobody finds
 * out. That is the whole reason this screen exists rather than a command that
 * writes a file: the model drafts, the backend proves each claim against the
 * source, and a person signs off before anything is exported.
 *
 * So the review table leads with the validator's verdict, not the question. A
 * row marked "to check" failed a check and says which; a row marked "grounded"
 * has had every answer key found verbatim in the document, every cited section
 * confirmed, every computed figure recomputed, and has been scored against its
 * own reference answer using the offline harness's own scorer.
 */

import { useCallback, useMemo, useState } from 'react'

import { GenerationPanel } from './GenerationPanel'
import { GoldenRowEditor } from './GoldenRowEditor'
import { EmptyState } from '../../components/EmptyState'
import { RelativeTime } from '../../components/RelativeTime'
import { Spinner } from '../../components/Spinner'
import { goldenExportUrl } from '../../api/client'
import type { GoldenRowStatus } from '../../api/types'
import { useGoldenRun } from '../../hooks/useGoldenRun'
import { useGoldenSets } from '../../hooks/useGoldenSets'
import { useSources } from '../../hooks/useSources'

/** Row filters, mirroring the pill nav the Sources screen uses. */
type Filter = 'all' | GoldenRowStatus | 'accepted' | 'dropped'

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'flagged', label: 'To check' },
  { value: 'valid', label: 'Grounded' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'dropped', label: 'Dropped' },
]

export function GoldenView() {
  const sets = useGoldenSets()
  const sources = useSources()
  const [filter, setFilter] = useState<Filter>('all')
  const [slugDraft, setSlugDraft] = useState('')

  // Refresh the listing when a run finishes, so the new set appears without a
  // reload. The panel offers to open it; this only makes it exist in the list.
  const onFinished = useCallback(() => sets.refresh(), [sets])
  const run = useGoldenRun(onFinished)

  const selected = sets.selected
  const rows = useMemo(() => {
    if (selected === null) return []
    return selected.rows.filter((row) => {
      if (filter === 'all') return true
      if (filter === 'accepted' || filter === 'dropped') return row.review === filter
      return row.status === filter
    })
  }, [filter, selected])

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Golden sets</h1>
          <p className="mt-1 text-sm text-slate-500">
            Evaluation questions drafted from a source document, with every claim
            checked back against it. The model proposes; nothing is trusted until
            it is grounded and you have signed off.
          </p>
        </div>
        <button
          type="button"
          onClick={sets.refresh}
          className="shrink-0 rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700"
        >
          Refresh
        </button>
      </header>

      <GenerationPanel
        sources={sources.sources}
        running={run.running}
        progress={run.progress}
        rowCount={run.rows.length}
        failures={run.failures}
        summary={run.summary}
        error={run.error}
        onStart={(sourceKey, density) => void run.start({ source_key: sourceKey, density })}
        onStop={() => void run.stop()}
        onDismiss={run.reset}
        onOpenSet={sets.open}
      />

      {sets.error !== null ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {sets.error}
        </p>
      ) : null}

      {sets.loading ? (
        <p className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner />
          Loading golden sets…
        </p>
      ) : null}

      {!sets.loading && sets.sets.length === 0 ? (
        <EmptyState
          title="No golden sets yet"
          hint="Pick a source file above and generate one. It takes about a dozen model calls."
        />
      ) : null}

      {sets.sets.length > 0 ? (
        <table className="mb-6 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
              <th className="py-2">File</th>
              <th className="py-2">Exports as</th>
              <th className="py-2">Rows</th>
              <th className="py-2">Grounded</th>
              <th className="py-2">Accepted</th>
              <th className="py-2">Generated</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {sets.sets.map((set) => (
              <tr key={set.set_id} className="border-b border-slate-100">
                <td className="py-2 text-slate-900">{set.source_key}</td>
                <td className="py-2 font-mono text-xs text-slate-500">{set.slug}.jsonl</td>
                <td className="py-2 text-slate-700">{set.row_count}</td>
                <td className="py-2 text-slate-700">
                  {set.valid_count}
                  {set.row_count > set.valid_count ? (
                    <span className="text-state-stale">
                      {' '}
                      ({set.row_count - set.valid_count} to check)
                    </span>
                  ) : null}
                </td>
                <td className="py-2 text-slate-700">{set.accepted_count}</td>
                <td className="py-2 text-slate-500">
                  {set.created_at !== null ? <RelativeTime value={set.created_at} /> : '—'}
                </td>
                <td className="py-2 text-right">
                  <button
                    type="button"
                    onClick={() => {
                      setSlugDraft(set.slug)
                      sets.open(set.set_id)
                    }}
                    className="mr-2 rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700"
                  >
                    Review
                  </button>
                  <a
                    href={goldenExportUrl(set.set_id)}
                    className="mr-2 rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700"
                  >
                    Download
                  </a>
                  <button
                    type="button"
                    onClick={() => void sets.remove(set.set_id)}
                    className="text-xs text-slate-400 hover:text-state-orphaned"
                  >
                    Withdraw
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {sets.loadingSet ? (
        <p className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner />
          Loading rows…
        </p>
      ) : null}

      {selected !== null ? (
        <section>
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">
                {selected.source_key}
              </h2>
              <p className="mt-0.5 text-xs text-slate-500">
                {selected.row_count} rows · {selected.valid_count} grounded ·{' '}
                {selected.accepted_count} accepted · drafted by {selected.model}
              </p>
            </div>

            <div className="flex items-end gap-2">
              <label>
                <span className="mb-1 block text-xs font-medium text-slate-600">
                  Exports as
                </span>
                <div className="flex items-center gap-1">
                  <input
                    value={slugDraft}
                    onChange={(event) => setSlugDraft(event.target.value)}
                    className="w-56 rounded border border-slate-300 px-2 py-1.5 font-mono text-xs"
                  />
                  <span className="font-mono text-xs text-slate-400">.jsonl</span>
                  <button
                    type="button"
                    onClick={() => void sets.rename(slugDraft)}
                    disabled={slugDraft === '' || slugDraft === selected.slug}
                    className="rounded border border-slate-300 px-2 py-1.5 text-xs font-medium text-slate-700 disabled:opacity-40"
                  >
                    Rename
                  </button>
                </div>
              </label>
              <button
                type="button"
                onClick={sets.close}
                className="text-xs text-slate-400 hover:text-slate-600"
              >
                Close
              </button>
            </div>
          </div>

          <nav className="mb-3 flex gap-1">
            {FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setFilter(option.value)}
                aria-current={filter === option.value ? 'true' : undefined}
                className={`rounded-full px-3 py-1 text-xs font-medium ${
                  filter === option.value
                    ? 'bg-slate-900 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {option.label}
              </button>
            ))}
          </nav>

          <p className="mb-3 text-xs text-slate-400">
            Save it to <code>evals/golden/{selected.slug}.jsonl</code> and score a
            run against it with{' '}
            <code>
              python evals/run_eval.py &lt;predictions&gt;.jsonl --golden
              evals/golden/{selected.slug}.jsonl
            </code>
          </p>

          {rows.length === 0 ? (
            <EmptyState title="No rows match this filter" hint="Try All." />
          ) : null}

          {rows.map((row) => (
            <GoldenRowEditor
              key={row.row_id}
              row={row}
              sections={selected.sections}
              saving={sets.saving === row.row_id}
              onUpdate={(update) => sets.updateRow(row.row_id, update)}
            />
          ))}
        </section>
      ) : null}
    </div>
  )
}
