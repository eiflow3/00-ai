/**
 * Drives a chat request and folds its event stream into an answer.
 *
 * The stream's ordering is the reason this screen exists: the retrieved chunks
 * arrive as one event before any answer text, so citations can be rendered
 * while the answer is still being written.
 */

import { useCallback, useRef, useState } from 'react'

import { streamChat } from '../api/client'
import type {
  BlockedEventData,
  ChatRequest,
  GovernanceEventData,
  RetrievedChunk,
  StageEventData,
  UsageEventData,
} from '../api/types'

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
  /**
   * Each pipeline step and how long it took, in the order they ran.
   *
   * Never filtered or relabelled here: the server names its own stages, so a
   * step added to the pipeline shows up without a change on this side.
   */
  stages: StageEventData[]
  /** Answer text so far, appended delta by delta. */
  answer: string
  /** Tokens and cost, present only once the stream has closed. */
  usage: UsageEventData | null
  streaming: boolean
  /** True once retrieval has reported, so an empty citation list is meaningful. */
  retrieved: boolean
  /**
   * What governance found — one entry after the question is screened, a
   * second after the answer is. Counts only; never the matched values.
   */
  governance: GovernanceEventData[]
  /**
   * Policy refused the question or the answer. A verdict, not a failure:
   * the stream ended cleanly with no answer text.
   */
  blocked: BlockedEventData | null
  /** A degraded stage — the answer still streams, just ungrounded. */
  warning: string | null
  /** The provider refused or failed, so there is no answer at all. */
  failure: string | null
  /** A failure that prevented an answer entirely. */
  error: string | null
  /**
   * Ask a question, naming who answers and which chunking to answer from.
   *
   * `chunk_variant` is what makes an A/B comparison possible: two of these
   * hooks, the same question and model, different variants, and the only thing
   * that can explain a difference in the answers is how the file was cut.
   */
  ask: (
    query: string,
    options?: Pick<ChatRequest, 'provider' | 'model' | 'chunk_variant' | 'governance_mode'>,
  ) => Promise<void>
  cancel: () => void
}

export function useChat(): UseChatResult {
  const [question, setQuestion] = useState('')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [citations, setCitations] = useState<RetrievedChunk[]>([])
  const [stages, setStages] = useState<StageEventData[]>([])
  const [answer, setAnswer] = useState('')
  const [usage, setUsage] = useState<UsageEventData | null>(null)
  const [governance, setGovernance] = useState<GovernanceEventData[]>([])
  const [blocked, setBlocked] = useState<BlockedEventData | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [retrieved, setRetrieved] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const controller = useRef<AbortController | null>(null)

  const ask = useCallback(async (
    query: string,
    options?: Pick<ChatRequest, 'provider' | 'model' | 'chunk_variant' | 'governance_mode'>,
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
    setStages([])
    setAnswer('')
    setUsage(null)
    setGovernance([])
    setBlocked(null)
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

          case 'stage': {
            // A stage reports twice — starting, then ending with its duration.
            // Both carry the same sequence, so the second replaces the first
            // rather than adding a row.
            const stage = event.data
            setStages((current) => {
              const at = current.findIndex((seen) => seen.sequence === stage.sequence)
              if (at === -1) return [...current, stage]
              const next = [...current]
              next[at] = stage
              return next
            })
            break
          }

          case 'governance':
            // Two arrive per request: the question's screening, then the
            // answer's. Appended in order so the report renders both.
            setGovernance((current) => [...current, event.data])
            break

          case 'blocked':
            // Terminal: policy refused the content, and nothing follows.
            setBlocked(event.data)
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
    stages,
    answer,
    usage,
    governance,
    blocked,
    streaming,
    retrieved,
    warning,
    failure,
    error,
    ask,
    cancel,
  }
}
