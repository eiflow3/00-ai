/**
 * Drives a chat request and folds its event stream into an answer.
 *
 * The stream's ordering is the reason this screen exists: the retrieved chunks
 * arrive as one event before any answer text, so citations can be rendered
 * while the answer is still being written.
 */

import { useCallback, useRef, useState } from 'react'

import { streamChat } from '../api/client'
import type { RetrievedChunk, UsageEventData } from '../api/types'

export interface UseChatResult {
  /** The question the current answer belongs to. */
  question: string
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
  /** A failure that prevented an answer entirely. */
  error: string | null
  ask: (query: string) => Promise<void>
  cancel: () => void
}

export function useChat(): UseChatResult {
  const [question, setQuestion] = useState('')
  const [citations, setCitations] = useState<RetrievedChunk[]>([])
  const [answer, setAnswer] = useState('')
  const [usage, setUsage] = useState<UsageEventData | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [retrieved, setRetrieved] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const controller = useRef<AbortController | null>(null)

  const ask = useCallback(async (query: string) => {
    const trimmed = query.trim()
    if (!trimmed) return

    // Asking again mid-stream abandons the previous answer.
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort

    setQuestion(trimmed)
    setCitations([])
    setAnswer('')
    setUsage(null)
    setRetrieved(false)
    setWarning(null)
    setError(null)
    setStreaming(true)

    try {
      for await (const event of streamChat({ query: trimmed }, abort.signal)) {
        switch (event.event) {
          case 'retrieval':
            setCitations(event.data.chunks)
            setRetrieved(true)
            break

          case 'message':
            // Deltas are appended verbatim; the reader preserves newlines.
            setAnswer((current) => current + event.data)
            break

          case 'error':
            // Non-fatal: retrieval failed, so the answer is ungrounded.
            setWarning(`${event.data.stage}: ${event.data.message}`)
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
    citations,
    answer,
    usage,
    streaming,
    retrieved,
    warning,
    error,
    ask,
    cancel,
  }
}
