/**
 * One prompt, open for editing.
 *
 * The variables are listed beside the box rather than buried in help text,
 * because they are the contract: the backend refuses a template naming a value
 * it cannot supply, so knowing the list is the difference between an edit that
 * saves and one that is rejected.
 *
 * Nothing is written until Save. A draft that diverges from what is stored is
 * marked as unsaved, so leaving the tab with a half-finished edit cannot be
 * mistaken for having made it.
 */

import { useState } from 'react'

import type { Prompt } from '../../api/types'
import { RelativeTime } from '../../components/RelativeTime'
import { Spinner } from '../../components/Spinner'

interface PromptEditorProps {
  prompt: Prompt
  /** True while this prompt in particular is being written. */
  saving: boolean
  onSave: (template: string) => Promise<string | null>
  onReset: () => Promise<string | null>
}

/** Rows to give the textarea, so a long template opens fully visible. */
function rowsFor(template: string): number {
  return Math.min(24, Math.max(4, template.split('\n').length + 1))
}

export function PromptEditor({ prompt, saving, onSave, onReset }: PromptEditorProps) {
  const [draft, setDraft] = useState(prompt.template)
  const [rejected, setRejected] = useState<string | null>(null)
  const [stored, setStored] = useState(prompt.template)

  // Re-syncs when a write returns different text than was typed — saving the
  // shipped default, for instance, comes back as a reset. Adjusted during the
  // render that brings the new text in rather than in an effect afterwards, so
  // the box is never painted holding the previous value.
  if (stored !== prompt.template) {
    setStored(prompt.template)
    setDraft(prompt.template)
    setRejected(null)
  }

  const dirty = draft !== prompt.template
  const atDefault = prompt.template === prompt.default_template

  async function handleSave() {
    setRejected(await onSave(draft))
  }

  async function handleReset() {
    setRejected(await onReset())
  }

  return (
    <section className="mb-4 rounded-lg border border-slate-200 bg-white">
      <header className="border-b border-slate-100 px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-900">{prompt.label}</h2>

          <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
            {prompt.id}
          </code>

          {prompt.edited ? (
            <span
              className="rounded-full bg-state-stale-soft px-2 py-0.5 text-xs font-medium text-state-stale"
              title="Overridden here — it no longer follows the default in the code."
            >
              Edited
            </span>
          ) : (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
              Default
            </span>
          )}

          {prompt.updated_at ? (
            <span className="ml-auto text-xs">
              <RelativeTime value={prompt.updated_at} />
            </span>
          ) : null}
        </div>

        <p className="mt-1.5 text-sm text-slate-500">{prompt.description}</p>
        <p className="mt-1 text-xs text-slate-400">{prompt.applies_when}</p>
      </header>

      <div className="px-5 py-4">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={rowsFor(draft)}
          spellCheck={false}
          aria-label={`${prompt.label} template`}
          placeholder={
            prompt.optional ? 'Empty — this prompt is not sent.' : undefined
          }
          className="w-full resize-y rounded-md border border-slate-200 px-3 py-2 font-mono text-xs leading-relaxed text-slate-800 outline-none focus:border-slate-400"
        />

        {prompt.variables.length > 0 ? (
          <dl className="mt-3 space-y-1">
            {prompt.variables.map((variable) => (
              <div key={variable.name} className="flex gap-2 text-xs">
                <dt className="shrink-0">
                  <button
                    type="button"
                    // Inserting beats typing: the braces are what the backend
                    // parses, and a missing one is the commonest rejection.
                    onClick={() => setDraft((current) => `${current}{${variable.name}}`)}
                    title="Add to the template"
                    className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-slate-600 hover:bg-slate-200"
                  >
                    {`{${variable.name}}`}
                  </button>
                </dt>
                <dd className="text-slate-400">
                  {variable.description}
                  {variable.required ? (
                    <span className="ml-1 font-medium text-slate-500">Required.</span>
                  ) : null}
                </dd>
              </div>
            ))}
          </dl>
        ) : null}

        {rejected ? (
          <p className="mt-3 rounded-md border border-state-orphaned-soft bg-state-orphaned-soft px-3 py-2 text-xs text-state-orphaned">
            {rejected}
          </p>
        ) : null}

        <div className="mt-4 flex items-center gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={!dirty || saving}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-30"
          >
            Save
          </button>

          <button
            type="button"
            onClick={() => setDraft(prompt.template)}
            disabled={!dirty || saving}
            className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-30"
          >
            Discard
          </button>

          <button
            type="button"
            onClick={handleReset}
            disabled={atDefault || saving}
            title="Restore the text this prompt ships with"
            className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-30"
          >
            Reset to default
          </button>

          {saving ? <Spinner /> : null}

          {dirty && !saving ? (
            <span className="text-xs text-state-stale">Unsaved</span>
          ) : null}
        </div>
      </div>
    </section>
  )
}
