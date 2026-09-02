/**
 * One answer, with the evidence that produced it.
 *
 * Extracted from the chat screen so the same rendering serves a single answer
 * at full width and two answers side by side. Two columns that differed in how
 * they showed a citation or a timing would invite a conclusion about the
 * chunking that was really about the markup.
 */

import { Citations } from './Citations'
import { EvaluatePanel } from './EvaluatePanel'
import { StageTimeline } from './StageTimeline'
import { UsageBar } from './UsageBar'
import { Spinner } from '../../components/Spinner'
import type { UseChatResult } from '../../hooks/useChat'

interface AnswerColumnProps {
  chat: UseChatResult
  /** Which chunking answered. Shown only when something is being compared. */
  label?: string
  /** What to call the provider when it fails. */
  providerLabel: string
}

export function AnswerColumn({ chat, label, providerLabel }: AnswerColumnProps) {
  return (
    <section className="min-w-0">
      {label ? (
        <h2 className="mb-2 border-b border-slate-200 pb-1.5 text-xs font-medium tracking-wide text-slate-500 uppercase">
          {label}
        </h2>
      ) : null}

      {chat.warning ? (
        <p className="mb-4 rounded-lg border border-state-stale-soft bg-state-stale-soft px-4 py-3 text-sm text-state-stale">
          Retrieval failed, so this answer is ungrounded — {chat.warning}
        </p>
      ) : null}

      {chat.failure ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {providerLabel} could not answer — {chat.failure}
        </p>
      ) : null}

      {chat.error ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {chat.error}
        </p>
      ) : null}

      {/* Above the citations because it starts filling in before they exist. */}
      <StageTimeline stages={chat.stages} />

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

      {/* The exchange is already recorded; this is where it gets judged. */}
      <EvaluatePanel traceId={chat.traceId} streaming={chat.streaming} />
    </section>
  )
}
