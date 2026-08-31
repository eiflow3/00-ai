/**
 * Judge the answer that was just given.
 *
 * Nothing is recorded until Save is pressed — the trace behind it already
 * exists either way, so an unevaluated answer costs nothing and an evaluated
 * one is a deliberate act.
 *
 * The control asks about retrieval and the answer *separately*, because that is
 * the question the whole feature exists to settle: an answer can be wrong
 * because the right passage was never found, or wrong despite having been given
 * it, and those are different bugs with different fixes.
 */

import { useState } from 'react'

import { TARGET_LABEL, VerdictBadge } from './VerdictBadge'
import { createEvaluation } from '../../api/client'
import type { EvaluationTarget, Verdict } from '../../api/types'
import { useEvaluationOptions } from '../../hooks/useEvaluationOptions'

interface EvaluatePanelProps {
  /** The exchange being judged; null until the stream opens. */
  traceId: string | null
  /** True while the answer is still arriving — judging it early is premature. */
  streaming: boolean
}

/** The stages offered, headline first. */
const TARGETS: EvaluationTarget[] = ['overall', 'retrieval', 'generation']

/** What each row is asking about, in the user's terms. */
const TARGET_QUESTION: Record<EvaluationTarget, string> = {
  overall: 'Was this exchange useful?',
  retrieval: 'Were the right chunks found?',
  generation: 'Did the answer use them faithfully?',
}

const VERDICTS: Verdict[] = ['good', 'partial', 'bad']

type Verdicts = Partial<Record<EvaluationTarget, Verdict>>

export function EvaluatePanel({ traceId, streaming }: EvaluatePanelProps) {
  const catalog = useEvaluationOptions()

  const [open, setOpen] = useState(false)
  const [verdicts, setVerdicts] = useState<Verdicts>({})
  const [tags, setTags] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<Verdicts | null>(null)
  const [error, setError] = useState<string | null>(null)

  // A new answer replaces the old one in place, so the panel has to forget the
  // previous verdict rather than offer it against a different exchange.
  const [judgedTrace, setJudgedTrace] = useState<string | null>(null)
  if (traceId !== judgedTrace) {
    setJudgedTrace(traceId)
    setOpen(false)
    setVerdicts({})
    setTags([])
    setNote('')
    setSaved(null)
    setError(null)
  }

  if (!traceId) return null

  const rated = TARGETS.filter((target) => verdicts[target] !== undefined)
  // Reasons are only asked for where something went wrong; a good verdict needs
  // no explanation, and offering chips for one invites noise.
  const explainable = rated.filter((target) => verdicts[target] !== 'good')

  function setVerdict(target: EvaluationTarget, verdict: Verdict) {
    setVerdicts((current) => {
      const next = { ...current }
      // Clicking the selected verdict again clears the row, which is how a
      // stage is left unjudged rather than judged "good" by accident.
      if (next[target] === verdict) delete next[target]
      else next[target] = verdict
      return next
    })
  }

  function toggleTag(tagId: string) {
    setTags((current) =>
      current.includes(tagId)
        ? current.filter((id) => id !== tagId)
        : [...current, tagId],
    )
  }

  async function save() {
    if (!traceId || rated.length === 0) return

    setSaving(true)
    setError(null)

    try {
      // One judgement per rated stage. The note is attached to each, so a
      // judgement read on its own still carries the reasoning.
      for (const target of rated) {
        const forTarget = catalog
          .tagsFor(target)
          .filter((tag) => tags.includes(tag.id))
          .map((tag) => tag.id)

        await createEvaluation(traceId, {
          target,
          verdict: verdicts[target] as Verdict,
          tags: forTarget,
          note: note.trim(),
        })
      }

      setSaved(verdicts)
      setOpen(false)
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  // --- Saved: collapse to what was recorded --------------------------------

  if (saved && !open) {
    return (
      <div className="mt-6 flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
        <span className="text-sm text-slate-500">Evaluated</span>
        {TARGETS.filter((target) => saved[target]).map((target) => (
          <VerdictBadge key={target} target={target} verdict={saved[target] as Verdict} />
        ))}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="ml-auto text-sm font-medium text-slate-500 underline-offset-2 hover:text-slate-800 hover:underline"
        >
          Judge again
        </button>
      </div>
    )
  }

  // --- Closed: one button, out of the way ----------------------------------

  if (!open) {
    return (
      <div className="mt-6">
        <button
          type="button"
          onClick={() => setOpen(true)}
          disabled={streaming}
          className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40"
        >
          Evaluate this answer
        </button>
        {streaming ? (
          <span className="ml-2 text-xs text-slate-400">available once it finishes</span>
        ) : null}
      </div>
    )
  }

  // --- Open: the judgement itself ------------------------------------------

  return (
    <section className="mt-6 rounded-lg border border-slate-200 bg-white p-4">
      <header className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-slate-900">Evaluate this answer</h2>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-xs text-slate-400 hover:text-slate-600"
        >
          Close
        </button>
      </header>

      {catalog.error ? (
        <p className="mb-3 text-xs text-state-stale">
          Could not load the reason list — {catalog.error}
        </p>
      ) : null}

      <div className="space-y-2.5">
        {TARGETS.map((target) => (
          <div key={target} className="flex flex-wrap items-center gap-2">
            <div className="w-40 shrink-0">
              <span className="text-sm font-medium text-slate-700">
                {TARGET_LABEL[target]}
              </span>
              <p className="text-xs text-slate-400">{TARGET_QUESTION[target]}</p>
            </div>

            {VERDICTS.map((verdict) => (
              <button
                key={verdict}
                type="button"
                aria-pressed={verdicts[target] === verdict}
                onClick={() => setVerdict(target, verdict)}
                className={`rounded-md border px-2.5 py-1 text-xs font-medium capitalize ${
                  verdicts[target] === verdict
                    ? 'border-slate-900 bg-slate-900 text-white'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-100'
                }`}
              >
                {verdict}
              </button>
            ))}

            {verdicts[target] ? (
              <span className="text-xs text-slate-300">click again to unset</span>
            ) : (
              <span className="text-xs text-slate-300">not judged</span>
            )}
          </div>
        ))}
      </div>

      {explainable.map((target) => (
        <div key={target} className="mt-3 border-t border-slate-100 pt-3">
          <p className="mb-1.5 text-xs font-medium tracking-wide text-slate-400 uppercase">
            What went wrong — {TARGET_LABEL[target].toLowerCase()}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {catalog.tagsFor(target).map((tag) => (
              <button
                key={tag.id}
                type="button"
                title={tag.hint}
                aria-pressed={tags.includes(tag.id)}
                onClick={() => toggleTag(tag.id)}
                className={`rounded-full border px-2.5 py-1 text-xs ${
                  tags.includes(tag.id)
                    ? 'border-slate-800 bg-slate-100 font-medium text-slate-800'
                    : 'border-slate-200 text-slate-500 hover:bg-slate-50'
                }`}
              >
                {tag.label}
              </button>
            ))}
          </div>
        </div>
      ))}

      <textarea
        value={note}
        onChange={(event) => setNote(event.target.value)}
        rows={2}
        placeholder="Anything the chips do not cover…"
        className="mt-3 w-full rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none placeholder:text-slate-400 focus:border-slate-400"
      />

      {error ? (
        <p className="mt-2 text-xs text-state-orphaned">Could not save — {error}</p>
      ) : null}

      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving || rated.length === 0}
          className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-30"
        >
          {saving ? 'Saving…' : `Save ${rated.length || ''} verdict${rated.length === 1 ? '' : 's'}`}
        </button>
        <span className="text-xs text-slate-400">
          {rated.length === 0
            ? 'Judge at least one row.'
            : 'Saved to the trace, with the chunks that produced this answer.'}
        </span>
      </div>
    </section>
  )
}
