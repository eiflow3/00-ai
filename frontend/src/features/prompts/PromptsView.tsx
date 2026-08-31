/**
 * The prompts screen: every instruction the pipeline sends, open for editing.
 *
 * These were constants in the backend until now, which made the wording that
 * decides how an answer is grounded the one part of the pipeline you could not
 * change without a deploy — while being the part you change most while tuning
 * retrieval. They are records now, and this is where they are read and written.
 *
 * The editors and the assembled result sit on one screen deliberately. A chunk
 * format judged on its own tells you nothing about the request it produces once
 * it has been repeated once per retrieved chunk.
 */

import { useState } from 'react'

import { AssembledPrompt } from './AssembledPrompt'
import { PromptEditor } from './PromptEditor'
import { EmptyState } from '../../components/EmptyState'
import { Spinner } from '../../components/Spinner'
import { usePromptPreview, usePrompts } from '../../hooks/usePrompts'

/** Question the preview is rendered around. Any question would do. */
const SAMPLE_QUERY = 'What was revenue in FY2025?'

export function PromptsView() {
  const [chunkCount, setChunkCount] = useState(2)
  const [grounded, setGrounded] = useState(true)

  const prompts = usePrompts()
  const preview = usePromptPreview(
    { query: SAMPLE_QUERY, chunk_count: chunkCount, grounded },
    prompts.version,
  )

  const edited = prompts.prompts.filter((prompt) => prompt.edited).length

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-5">
        <h1 className="text-lg font-semibold text-slate-900">Prompts</h1>
        <p className="mt-1 text-sm text-slate-500">
          Every instruction the pipeline sends, in the order it sends them. Edits
          take effect on the next question — no restart, no deploy.
        </p>
        {edited > 0 ? (
          <p className="mt-2 text-xs text-state-stale">
            {edited} prompt(s) overridden here. Every answer since is written under
            that wording, not the default in the code.
          </p>
        ) : null}
      </header>

      {prompts.error ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          Could not load the prompts — {prompts.error}
        </p>
      ) : null}

      {prompts.loading ? (
        <p className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner />
          Loading prompts…
        </p>
      ) : null}

      {!prompts.loading && !prompts.error && prompts.prompts.length === 0 ? (
        <EmptyState
          title="No prompts registered"
          hint="The backend serves this list, so an empty one means nothing is wired up."
        />
      ) : null}

      {prompts.prompts.map((prompt) => (
        <PromptEditor
          key={prompt.id}
          prompt={prompt}
          saving={prompts.saving === prompt.id}
          onSave={(template) => prompts.save(prompt.id, template)}
          onReset={() => prompts.reset(prompt.id)}
        />
      ))}

      {prompts.prompts.length > 0 ? (
        <AssembledPrompt
          preview={preview.preview}
          loading={preview.loading}
          error={preview.error}
          chunkCount={chunkCount}
          onChunkCount={setChunkCount}
          grounded={grounded}
          onGrounded={setGrounded}
        />
      ) : null}
    </div>
  )
}
