/**
 * The chat screen: ask a question, watch it get answered from the index.
 *
 * The retrieved chunks land before any answer text, so the citations appear
 * first and the answer streams in beneath them.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'

import { Citations } from './Citations'
import { UsageBar } from './UsageBar'
import { EmptyState } from '../../components/EmptyState'
import { Spinner } from '../../components/Spinner'
import { useChat } from '../../hooks/useChat'

export function ChatView() {
  const [draft, setDraft] = useState('')
  const chat = useChat()

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    void chat.ask(draft)
  }

  const hasAnswered = chat.question !== ''

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-5">
        <h1 className="text-lg font-semibold text-slate-900">Chat</h1>
        <p className="mt-1 text-sm text-slate-500">
          Answered from the chunks embedded in the vector index.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="mb-6 flex gap-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask something about your indexed files…"
          className="min-w-0 flex-1 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none placeholder:text-slate-400 focus:border-slate-400"
        />
        {chat.streaming ? (
          <button
            type="button"
            onClick={chat.cancel}
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
            Ask
          </button>
        )}
      </form>

      {chat.error ? (
        <p className="rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {chat.error}
        </p>
      ) : null}

      {chat.warning ? (
        <p className="mb-4 rounded-lg border border-state-stale-soft bg-state-stale-soft px-4 py-3 text-sm text-state-stale">
          Retrieval failed, so this answer is ungrounded — {chat.warning}
        </p>
      ) : null}

      {!hasAnswered && !chat.error ? (
        <EmptyState
          title="No question asked yet"
          hint="Index a file on the Sources tab, then ask about it here."
        />
      ) : null}

      {hasAnswered ? (
        <>
          <p className="mb-4 text-sm font-medium text-slate-800">{chat.question}</p>

          <Citations chunks={chat.citations} retrieved={chat.retrieved} />

          {/* whitespace-pre-wrap keeps the line breaks the stream sent. */}
          <div className="text-sm leading-relaxed whitespace-pre-wrap text-slate-700">
            {chat.answer}
            {chat.streaming ? (
              <span className="ml-1 inline-block h-4 w-1.5 animate-pulse bg-slate-400 align-text-bottom" />
            ) : null}
          </div>

          {chat.streaming && chat.answer === '' && chat.retrieved ? (
            <p className="flex items-center gap-2 text-sm text-slate-400">
              <Spinner />
              Writing…
            </p>
          ) : null}

          {chat.usage ? <UsageBar usage={chat.usage} /> : null}
        </>
      ) : null}
    </div>
  )
}
