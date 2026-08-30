/**
 * Server-Sent Events over POST.
 *
 * The browser's `EventSource` only issues GET requests, and both streaming
 * endpoints here are POST with a JSON body. So the response body is read as a
 * stream and the SSE framing is parsed by hand.
 *
 * Four framing details matter, and getting any of them wrong corrupts the
 * output silently rather than throwing:
 *
 *   1. A payload containing newlines arrives as several `data:` lines, which
 *      must be accumulated and rejoined — not overwritten.
 *   2. Lines are separated by CRLF, not LF.
 *   3. Lines beginning with ':' are comments (the server's `: ping` keepalive)
 *      and are not events.
 *   4. An event with no `event:` line is SSE's default `message` type, which is
 *      how the chat stream distinguishes answer text from metadata.
 */

/** One decoded SSE event: its name, and its raw (still unparsed) data. */
export interface RawSseEvent {
  event: string
  data: string
}

/** SSE's default event name, used when a frame carries no `event:` line. */
const DEFAULT_EVENT = 'message'

/** Frames are separated by a blank line — in either line ending. */
const FRAME_SEPARATOR = /\r\n\r\n|\n\n|\r\r/

/** Lines within a frame, in any of the three permitted line endings. */
const LINE_SEPARATOR = /\r\n|\n|\r/

/**
 * Parse one SSE frame into an event.
 *
 * @param frame - The raw text of a single frame, without its trailing blank line.
 * @returns The event, or null if the frame carried only comments.
 */
function parseFrame(frame: string): RawSseEvent | null {
  let event = DEFAULT_EVENT
  const dataLines: string[] = []

  for (const line of frame.split(LINE_SEPARATOR)) {
    // A leading colon marks a comment. The server sends these as keepalives.
    if (line.startsWith(':')) continue

    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    // The spec strips a single space after the colon, and only one.
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') {
      event = value
    } else if (field === 'data') {
      // Accumulate: a multi-line payload spans several `data:` lines.
      dataLines.push(value)
    }
    // `id` and `retry` are part of SSE but unused here.
  }

  if (dataLines.length === 0) return null

  return { event, data: dataLines.join('\n') }
}

/**
 * POST a JSON body and yield each SSE event as it arrives.
 *
 * @param url - Absolute URL of the streaming endpoint.
 * @param body - JSON request body.
 * @param signal - Abort signal, used to cancel the stream.
 * @returns An async iterable of decoded events.
 * @throws If the response is not OK, carrying the server's `detail` when present.
 */
export async function* postSse(
  url: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<RawSseEvent> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })

  // A failure before the stream opens comes back as a normal JSON error, so
  // surface the server's own message rather than a bare status code.
  if (!response.ok || !response.body) {
    let detail = `Request failed with status ${response.status}`
    try {
      const payload = await response.json()
      if (payload?.detail) detail = String(payload.detail)
    } catch {
      // Body was not JSON; the status-code message stands.
    }
    throw new Error(detail)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break

      // `stream: true` keeps a multi-byte character split across two network
      // chunks from decoding into a replacement character.
      buffer += decoder.decode(value, { stream: true })

      // Everything before the last frame separator is complete; the remainder
      // is a partial frame that the next read will finish.
      const frames = buffer.split(FRAME_SEPARATOR)
      buffer = frames.pop() ?? ''

      for (const frame of frames) {
        const parsed = parseFrame(frame)
        if (parsed) yield parsed
      }
    }

    // A stream that ends without a trailing blank line still has one event.
    const trailing = parseFrame(buffer)
    if (trailing) yield trailing
  } finally {
    // Releasing the lock lets the connection be torn down on abort.
    reader.releaseLock()
  }
}
