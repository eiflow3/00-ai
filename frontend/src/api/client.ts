/**
 * Typed access to the backend.
 *
 * Every request goes through here so the base URL, error shape and JSON
 * handling are defined once. Streaming endpoints return async iterables of
 * typed events; everything else returns a parsed body.
 */

import { postSse } from './sse'
import type {
  ChatEvent,
  ChatRequest,
  DeindexResponse,
  IndexEvent,
  IndexRequest,
  IndexState,
  SourceDetail,
  SourceStatus,
} from './types'

/** Backend origin. Overridden per environment; the default matches `npm run app:api`. */
const BASE_URL = import.meta.env?.VITE_API_BASE_URL ?? 'http://localhost:8000'

/**
 * The configured model rejects any temperature but 1.
 *
 * The endpoint's own default of 0.3 therefore fails outright. Pinned here so
 * the chat screen works, rather than changing the backend's default from the
 * client side — see the note in the project plan.
 */
const SUPPORTED_TEMPERATURE = 1

/** An error carrying the backend's own `detail` message where one was sent. */
export class ApiError extends Error {
  // Declared as a field rather than a constructor parameter property, which
  // `erasableSyntaxOnly` disallows.
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Build an absolute URL for a path, encoding an object key safely.
 *
 * Object keys contain slashes, which must survive as path separators, but a
 * key can also contain characters that need escaping.
 */
function url(path: string, params?: Record<string, string | undefined>): string {
  const target = new URL(BASE_URL + path)
  for (const [name, value] of Object.entries(params ?? {})) {
    if (value) target.searchParams.set(name, value)
  }
  return target.toString()
}

/**
 * Encode an object key for use in a path segment.
 *
 * The route is declared with a `:path` converter, so slashes stay literal
 * while everything else is percent-encoded.
 */
function encodeKey(sourceKey: string): string {
  return sourceKey.split('/').map(encodeURIComponent).join('/')
}

/** Issue a request and parse its JSON body, raising ApiError on failure. */
async function request<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const payload = await response.json()
      if (payload?.detail) detail = String(payload.detail)
    } catch {
      // Body was not JSON; the status-code message stands.
    }
    throw new ApiError(detail, response.status)
  }

  return (await response.json()) as T
}

/**
 * List every source file with the state of its embeddings.
 *
 * @param options.prefix - Restrict to keys beginning with this prefix.
 * @param options.state - Return only files in this state.
 */
export function listSources(options: {
  prefix?: string
  state?: IndexState
  signal?: AbortSignal
} = {}): Promise<SourceStatus[]> {
  return request<SourceStatus[]>(
    url('/sources', { prefix: options.prefix, state: options.state }),
    { signal: options.signal },
  )
}

/** Fetch one file's state together with every chunk indexed from it. */
export function getSource(sourceKey: string, signal?: AbortSignal): Promise<SourceDetail> {
  return request<SourceDetail>(url(`/sources/${encodeKey(sourceKey)}`), { signal })
}

/** Delete a file's vectors, leaving the file itself in object storage. */
export function deindexSource(sourceKey: string): Promise<DeindexResponse> {
  return request<DeindexResponse>(url(`/sources/${encodeKey(sourceKey)}/index`), {
    method: 'DELETE',
  })
}

/**
 * Run the data embedding pipeline, yielding each progress event.
 *
 * With no `keys`, the run covers everything under `prefix` that needs it.
 */
export async function* indexSources(
  body: IndexRequest,
  signal?: AbortSignal,
): AsyncGenerator<IndexEvent> {
  for await (const raw of postSse(url('/sources/index'), body, signal)) {
    // Every event on this stream is named and carries a JSON payload.
    yield { event: raw.event, data: JSON.parse(raw.data) } as IndexEvent
  }
}

/**
 * Ask a question, yielding retrieval, text and usage events as they arrive.
 *
 * Text deltas come through unnamed — SSE's default `message` type — so they
 * are passed along as raw strings while the named events are parsed as JSON.
 */
export async function* streamChat(
  body: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const payload: ChatRequest = { temperature: SUPPORTED_TEMPERATURE, ...body }

  for await (const raw of postSse(url('/chat'), payload, signal)) {
    if (raw.event === 'message') {
      yield { event: 'message', data: raw.data }
    } else {
      yield { event: raw.event, data: JSON.parse(raw.data) } as ChatEvent
    }
  }
}
