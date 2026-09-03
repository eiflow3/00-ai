/**
 * The chat screen: ask a question, watch it get answered from the index.
 *
 * The stream is ordered so the screen fills top-down as the request runs: the
 * pipeline's steps report themselves as they happen, the retrieved chunks land
 * before any answer text, and the answer streams in beneath them.
 *
 * It also answers a second question, once something has been indexed under a
 * chunking variant: *what does cutting the document differently do to this
 * answer?* Two columns, the same question, model and prompt, and only the
 * chunking differs — so anything that differs between them is the chunking's
 * doing. Which of the two is better overall is not decided here; that is what
 * the scoreboard on the Chunking tab is for.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'

import { AnswerColumn } from './AnswerColumn'
import { ModelPicker } from './ModelPicker'
import { VariantPicker } from './VariantPicker'
import { productionLabel } from './answering'
import type { GovernanceMode, ModelOption } from '../../api/types'
import { EmptyState } from '../../components/EmptyState'
import { GovernancePicker } from '../../components/GovernancePicker'
import { useChat } from '../../hooks/useChat'
import { useModels } from '../../hooks/useModels'
import { useProduction } from '../../hooks/useProduction'
import { useVariants } from '../../hooks/useVariants'

interface ChatViewProps {
  /** A variant the Chunking tab asked to open with, if any. */
  initialVariant?: string
}

export function ChatView({ initialVariant = '' }: ChatViewProps) {
  const [draft, setDraft] = useState('')
  // The user's explicit pick, if they have made one. The option in use is
  // derived from it rather than stored, so the default falls into place as
  // soon as the catalog arrives without an effect writing state.
  const [picked, setPicked] = useState<string | null>(null)
  // Same shape as the model above: the person's own pick if they have made
  // one, otherwise whatever the Chunking tab arrived with. Derived rather than
  // stored, so pressing Ask on a variant lands without an effect writing state.
  const [pickedVariant, setPickedVariant] = useState<string | null>(null)
  const [secondary, setSecondary] = useState<string | null>(null)
  // '' sends nothing, so the server's configured default applies.
  const [governanceMode, setGovernanceMode] = useState<GovernanceMode | ''>('')

  const primary = pickedVariant ?? initialVariant

  // Two streams rather than a list of them: a fixed pair can be plain hooks,
  // and two is the most a person can actually read side by side.
  const left = useChat()
  const right = useChat()
  const models = useModels()
  const variants = useVariants()
  const production = useProduction()

  const model: ModelOption | null =
    models.options.find((option) => option.model === picked && option.available) ??
    models.options.find((option) => option.available) ??
    null

  const comparing = secondary !== null
  const streaming = left.streaming || right.streaming

  function nameFor(variantId: string): string {
    return (
      variants.variants.find((variant) => variant.variant_id === variantId)?.label ??
      productionLabel(production.production)
    )
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()

    const options = {
      ...(model ? { provider: model.provider, model: model.model } : {}),
      // Sent only when a mode was actually picked; '' means the default.
      ...(governanceMode !== '' ? { governance_mode: governanceMode } : {}),
    }

    void left.ask(draft, { ...options, chunk_variant: primary })
    if (secondary !== null) {
      void right.ask(draft, { ...options, chunk_variant: secondary })
    }
  }

  const hasAnswered = left.question !== ''

  return (
    <div className={`mx-auto px-6 py-8 ${comparing ? 'max-w-6xl' : 'max-w-3xl'}`}>
      <header className="mb-5">
        <h1 className="text-lg font-semibold text-slate-900">Chat</h1>
        <p className="mt-1 text-sm text-slate-500">
          Answered from the chunks embedded in the vector index.
        </p>
      </header>

      <ModelPicker
        options={models.options}
        selected={model?.model ?? null}
        onSelect={(option) => setPicked(option.model)}
        disabled={streaming}
      />

      <VariantPicker
        variants={variants.variants}
        production={production.production}
        primary={primary}
        secondary={secondary}
        disabled={streaming}
        onPrimary={setPickedVariant}
        onSecondary={setSecondary}
      />

      <div className="mb-4">
        <GovernancePicker
          value={governanceMode}
          onChange={setGovernanceMode}
          disabled={streaming}
        />
      </div>

      {models.error ? (
        <p className="mb-4 text-xs text-state-stale">
          Could not load the model list — {models.error}
        </p>
      ) : null}

      <form onSubmit={handleSubmit} className="mb-6 flex gap-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask something about your indexed files…"
          className="min-w-0 flex-1 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none placeholder:text-slate-400 focus:border-slate-400"
        />
        {streaming ? (
          <button
            type="button"
            onClick={() => {
              left.cancel()
              right.cancel()
            }}
            className="rounded-md border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={draft.trim() === ''}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-30"
          >
            {comparing ? 'Ask both' : 'Ask'}
          </button>
        )}
      </form>

      {!hasAnswered && !left.error ? (
        <EmptyState
          title="No question asked yet"
          hint="Index a file on the Sources tab, then ask about it here."
        />
      ) : null}

      {hasAnswered ? (
        <>
          <p className="mb-4 text-sm font-medium text-slate-800">{left.question}</p>

          <div className={comparing ? 'grid gap-8 md:grid-cols-2' : ''}>
            <AnswerColumn
              chat={left}
              label={comparing ? nameFor(primary) : undefined}
              providerLabel={model?.provider_label ?? 'The model'}
            />
            {comparing ? (
              <AnswerColumn
                chat={right}
                label={nameFor(secondary)}
                providerLabel={model?.provider_label ?? 'The model'}
              />
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  )
}
