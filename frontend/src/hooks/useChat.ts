/**
 * Drives a chat request and folds its event stream into an answer.
 *
 * The stream's ordering is the reason this screen exists: the retrieved chunks
 * arrive as one event before any answer text, so citations can be rendered
 * while the answer is still being written.
 */

import { useCallback, useRef, useState } from 'react'

import { streamChat } from '../api/client'
import type { ChatRequest, RetrievedChunk, UsageEventData } from '../api/types'

export interface UseChatResult {
  /** The question the current answer belongs to. */
  question: string
  /**
   * Id this exchange is recorded under, available before the answer arrives.
   *
   * It is what an evaluation is filed against, so the evaluate control can be
   * rendered as soon as the stream opens rather than after it closes.
   */
  traceId: string | null
  /** Chunks that grounded the answer, best score first. */
  citations: RetrievedChunk[]
  /** Answer text so far, appended delta by delta. */
  answer: string
  /** Tokens and cost, present only once the stream has closed. */
  usage: UsageEventData | null
  streaming: boolean
  /** True once retrieval has reported, so an empty citation list is meaningful. */
  retrieved: boolean
  /** A degraded stage — the answer still streams, just ungrounded. */
  warning: string | null
  /** The provider refused or failed, so there is no answer at all. */
  failure: string | null
  /** A failure that prevented an answer entirely. */
  error: string | null
  /** Ask a question, optionally naming which provider and model should answer. */
  ask: (query: string, options?: Pick<ChatRequest, 'provider' | 'model'>) => Promise<void>
  cancel: () => void
}

export function useChat(): UseChatResult {
  const [question, setQuestion] = useState('')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [citations, setCitations] = useState<RetrievedChunk[]>([])
  const [answer, setAnswer] = useState('')
  const [usage, setUsage] = useState<UsageEventData | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [retrieved, setRetrieved] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const controller = useRef<AbortController | null>(null)

  const ask = useCallback(async (
    query: string,
    options?: Pick<ChatRequest, 'provider' | 'model'>,
  ) => {
    const trimmed = query.trim()
    if (!trimmed) return

    // Asking again mid-stream abandons the previous answer.
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort

    setQuestion(trimmed)
    setTraceId(null)
    setCitations([])
    setAnswer('')
    setUsage(null)
    setRetrieved(false)
    setWarning(null)
    setFailure(null)
    setError(null)
    setStreaming(true)

    try {
      for await (const event of streamChat({ query: trimmed, ...options }, abort.signal)) {
        switch (event.event) {
          case 'trace':
            // Arrives first, before retrieval has even run.
            setTraceId(event.data.trace_id)
            break

          case 'retrieval':
            setCitations(event.data.chunks)
            setRetrieved(true)
            break

          case 'message':
            // Deltas are appended verbatim; the reader preserves newlines.
            setAnswer((current) => current + event.data)
            break

          case 'error':
            // Retrieval failing is survivable — the answer streams ungrounded.
            // Generation failing is not: there will be no answer at all, and
            // the stream ends here. The two must not read the same on screen.
            if (event.data.stage === 'generation') {
              setFailure(event.data.message)
            } else {
              setWarning(`${event.data.stage}: ${event.data.message}`)
            }
            break

          case 'usage':
            setUsage(event.data)
            break
        }
      }
    } catch (cause: unknown) {
      if (!abort.signal.aborted) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      // A superseded request must not clear the newer one's streaming flag.
      if (controller.current === abort) setStreaming(false)
    }
  }, [])

  const cancel = useCallback(() => {
    controller.current?.abort()
    setStreaming(false)
  }, [])

  return {
    question,
    traceId,
    citations,
    answer,
    usage,
    streaming,
    retrieved,
    warning,
    failure,
    error,
    ask,
    cancel,
  }
}
