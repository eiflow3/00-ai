/**
 * The message list the current prompts would actually produce.
 *
 * Templates read differently apart than assembled: the chunk format is sent
 * once per retrieved chunk, wrapped in the block that carries them, ahead of a
 * system prompt that is sent once. Showing the result is the only way the tab
 * answers the question it exists for — what does the model actually receive?
 *
 * The controls change the shape of the request rather than the wording, so the
 * two paths that behave differently can both be seen: retrieval that found
 * nothing, and retrieval that was never asked for.
 */

import type { PromptPreview } from '../../api/types'
import { Spinner } from '../../components/Spinner'

interface AssembledPromptProps {
  preview: PromptPreview | null
  loading: boolean
  error: string | null
  chunkCount: number
  onChunkCount: (count: number) => void
  grounded: boolean
  onGrounded: (grounded: boolean) => void
}

/** Chunk counts worth previewing — none, one, and enough to show repetition. */
const CHUNK_COUNTS = [0, 1, 2, 3]

export function AssembledPrompt({
  preview,
  loading,
  error,
  chunkCount,
  onChunkCount,
  grounded,
  onGrounded,
}: AssembledPromptProps) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <header className="border-b border-slate-100 px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-900">Assembled request</h2>
          {loading ? <Spinner /> : null}
          {preview ? (
            <span className="ml-auto tabular text-xs text-slate-400">
              {preview.messages.length} message(s) · {preview.character_count} characters
            </span>
          ) : null}
        </div>

        <p className="mt-1.5 text-sm text-slate-500">
          Exactly what the model receives, rendered from the prompts above with
          stand-in chunks.
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-400">Retrieved chunks</span>
          <div className="flex rounded-md border border-slate-200">
            {CHUNK_COUNTS.map((count) => (
              <button
                key={count}
                type="button"
                onClick={() => onChunkCount(count)}
                aria-pressed={chunkCount === count}
                disabled={!grounded && count > 0}
                className={`tabular px-2.5 py-1 text-xs font-medium first:rounded-l-md last:rounded-r-md disabled:opacity-30 ${
                  chunkCount === count
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                {count}
              </button>
            ))}
          </div>

          <label className="ml-2 flex items-center gap-1.5 text-xs text-slate-500">
            <input
              type="checkbox"
              checked={!grounded}
              onChange={(event) => {
                onGrounded(!event.target.checked)
                // With RAG off there is nothing to retrieve, so a chunk count
                // above zero would be previewing a request that cannot happen.
                if (event.target.checked) onChunkCount(0)
              }}
            />
            RAG off
          </label>
        </div>
      </header>

      <div className="px-5 py-4">
        {error ? (
          <p className="rounded-md border border-state-orphaned-soft bg-state-orphaned-soft px-3 py-2 text-xs text-state-orphaned">
            {error}
          </p>
        ) : null}

        {preview?.messages.map((message, index) => (
          <article
            // Position is the identity here: two system messages can carry the
            // same role, and the list is rebuilt whole on every render anyway.
            key={index}
            className="mb-3 last:mb-0 rounded-md border border-slate-100 bg-slate-50"
          >
            <p className="border-b border-slate-100 px-3 py-1.5 text-xs font-medium text-slate-500">
              {message.role}
            </p>
            {/* whitespace-pre-wrap: the line breaks are part of the prompt. */}
            <pre className="overflow-x-auto px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap text-slate-700">
              {message.content}
            </pre>
          </article>
        ))}
      </div>
    </section>
  )
}
