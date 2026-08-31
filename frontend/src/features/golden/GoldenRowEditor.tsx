/**
 * One drafted row, open for review.
 *
 * The reviewer's job is not to read questions — it is to decide whether each
 * one can be trusted as an answer key. So the validator's findings lead, the
 * fields most often wrong are the ones you can edit, and the row's own working
 * is shown for a computed figure.
 *
 * Answer keys are the field to watch. They must appear in the source document
 * character for character, and a model that writes "$2.8 billion" where the
 * report says "2,833.0" produces a question no correct answer can ever pass.
 * Saving an edit re-checks it against the document, so the flag clears here or
 * it does not clear at all.
 *
 * Draft state is held locally and re-synced during render when the stored row
 * changes, the same way `PromptEditor` does it — no effect, no flicker.
 */

import { useState } from 'react'

import { IssueBadge, ReviewBadge } from './IssueBadge'
import { checkLabel } from './checks'
import { Spinner } from '../../components/Spinner'
import type { GoldenRow, GoldenRowUpdate } from '../../api/types'

interface GoldenRowEditorProps {
  row: GoldenRow
  /** Titles this set may cite, so a fix picks from the real outline. */
  sections: string[]
  saving: boolean
  onUpdate: (update: GoldenRowUpdate) => Promise<string | null>
}

/** Split a comma-separated field into the list the API expects. */
function toList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

export function GoldenRowEditor({ row, sections, saving, onUpdate }: GoldenRowEditorProps) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState(row)
  const [keysText, setKeysText] = useState(row.answer_keys.join(', '))
  const [stored, setStored] = useState(row)

  // Re-syncs when a save returns a row different from the one typed — the
  // backend re-checks an edit, so what comes back may carry new issues.
  // Adjusted during the render that brings the new row in rather than in an
  // effect afterwards, so the fields are never painted holding stale values.
  if (stored !== row) {
    setStored(row)
    setDraft(row)
    setKeysText(row.answer_keys.join(', '))
    setError(null)
  }

  const dirty =
    draft.question !== row.question ||
    draft.answer !== row.answer ||
    draft.note !== row.note ||
    keysText !== row.answer_keys.join(', ') ||
    draft.gold_sections.join('|') !== row.gold_sections.join('|')

  /** Send an update and surface whatever the backend said about it. */
  async function apply(update: GoldenRowUpdate) {
    setError(await onUpdate(update))
  }

  async function save() {
    await apply({
      question: draft.question,
      answer: draft.answer,
      note: draft.note,
      answer_keys: toList(keysText),
      gold_sections: draft.gold_sections,
    })
  }

  const dropped = row.review === 'dropped'

  return (
    <div
      className={`mb-2 rounded-lg border bg-white ${
        dropped ? 'border-slate-200 opacity-60' : 'border-slate-200'
      }`}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        <span className="w-12 shrink-0 font-mono text-xs text-slate-400">
          {row.question_id || '—'}
        </span>

        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          className="flex-1 text-left"
        >
          <p className="text-sm text-slate-900">{row.question}</p>
          <p className="mt-1 text-xs text-slate-500">
            {row.type} · {row.difficulty}
            {row.edited ? ' · edited' : ''}
          </p>
        </button>

        <div className="flex shrink-0 items-center gap-2">
          <ReviewBadge review={row.review} />
          <IssueBadge status={row.status} issues={row.issues} />
        </div>
      </div>

      {row.issues.length > 0 ? (
        <ul className="border-t border-slate-100 px-4 py-2">
          {row.issues.map((issue, index) => (
            <li key={`${issue.check}-${index}`} className="text-xs text-state-stale">
              <span className="font-medium">{checkLabel(issue.check)}</span> — {issue.detail}
            </li>
          ))}
        </ul>
      ) : null}

      {open ? (
        <div className="border-t border-slate-100 px-4 py-3">
          <label className="mb-1 block text-xs font-medium text-slate-600">Question</label>
          <textarea
            value={draft.question}
            onChange={(e) => setDraft({ ...draft, question: e.target.value })}
            rows={2}
            className="mb-3 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          />

          <label className="mb-1 block text-xs font-medium text-slate-600">
            Reference answer
          </label>
          <textarea
            value={draft.answer}
            onChange={(e) => setDraft({ ...draft, answer: e.target.value })}
            rows={3}
            className="mb-3 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          />

          <label className="mb-1 block text-xs font-medium text-slate-600">
            Answer keys — comma separated, verbatim from the document
          </label>
          <input
            value={keysText}
            onChange={(e) => setKeysText(e.target.value)}
            className="mb-1 w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm"
          />
          <p className="mb-3 text-xs text-slate-400">
            Copy figures exactly as the document writes them. "2,833.0" passes;
            "2833.0" and "$2.8 billion" never will.
          </p>

          <label className="mb-1 block text-xs font-medium text-slate-600">
            Sections this answer comes from
          </label>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {sections.map((section) => {
              const cited = draft.gold_sections.includes(section)
              return (
                <button
                  key={section}
                  type="button"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      gold_sections: cited
                        ? draft.gold_sections.filter((title) => title !== section)
                        : [...draft.gold_sections, section],
                    })
                  }
                  className={`rounded-full px-2.5 py-1 text-xs ${
                    cited
                      ? 'bg-slate-900 text-white'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                >
                  {section}
                </button>
              )
            })}
          </div>

          {row.numeric_answer !== null ? (
            <p className="mb-3 text-xs text-slate-500">
              Scored numerically against {row.numeric_answer}
              {row.numeric_tolerance !== null ? ` ± ${row.numeric_tolerance}` : ''}.
            </p>
          ) : null}

          {row.forbidden_keys.length > 0 ? (
            <p className="mb-3 text-xs text-slate-500">
              Fails if the answer contains: {row.forbidden_keys.join(', ')}
            </p>
          ) : null}

          {row.must_refuse ? (
            <p className="mb-3 text-xs text-slate-500">
              Must decline — the document does not state this.
            </p>
          ) : null}

          {row.derivation !== null ? (
            <p className="mb-3 rounded bg-slate-50 px-2 py-1.5 font-mono text-xs text-slate-600">
              {row.derivation.operator}({row.derivation.operands.join(', ')}) —{' '}
              {row.derivation.explanation}
            </p>
          ) : null}

          <label className="mb-1 block text-xs font-medium text-slate-600">
            Note — the trap this question sets
          </label>
          <input
            value={draft.note}
            onChange={(e) => setDraft({ ...draft, note: e.target.value })}
            className="mb-3 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          />

          {error ? (
            <p className="mb-3 rounded border border-state-orphaned-soft bg-state-orphaned-soft px-3 py-2 text-xs text-state-orphaned">
              {error}
            </p>
          ) : null}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={save}
              disabled={!dirty || saving}
              className="rounded bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
            >
              {saving ? <Spinner /> : null} Save and re-check
            </button>
            <button
              type="button"
              onClick={() => apply({ review: 'accepted' })}
              disabled={saving || row.review === 'accepted'}
              className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 disabled:opacity-40"
            >
              Accept
            </button>
            <button
              type="button"
              onClick={() => apply({ review: dropped ? 'pending' : 'dropped' })}
              disabled={saving}
              className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 disabled:opacity-40"
            >
              {dropped ? 'Restore' : 'Drop'}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
