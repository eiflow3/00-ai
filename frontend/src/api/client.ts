/**
 * Typed access to the backend.
 *
 * Every request goes through here so the base URL, error shape and JSON
 * handling are defined once. Streaming endpoints return async iterables of
 * typed events; everything else returns a parsed body.
 */

import { getSse, postSse } from './sse'
import type {
  ChatEvent,
  ChatRequest,
  ChunkPreviewResponse,
  ChunkStrategySpec,
  ChunkVariant,
  ChunkingConfig,
  DeindexResponse,
  DeleteResponse,
  EnqueueResponse,
  Evaluation,
  GoldenEnqueueResponse,
  GoldenEvent,
  GoldenEventWithCursor,
  GoldenOptions,
  GoldenRow,
  GoldenRowUpdate,
  GoldenRun,
  GoldenRunRequest,
  GoldenSet,
  GoldenSetDetail,
  EvaluationOptions,
  EvaluationRequest,
  EvaluationTarget,
  IndexEvent,
  IndexEventWithCursor,
  IndexRequest,
  IndexRun,
  IndexState,
  ModelOption,
  Prompt,
  PromptPreview,
  ProductionSpace,
  PromptPreviewRequest,
  ScoreEnqueueResponse,
  ScoreEvent,
  ScoreEventWithCursor,
  ScoreRun,
  SourceDetail,
  TableArtifact,
  TableListResponse,
  SourceStatus,
  VariantDeleteResponse,
  VariantScoreRequest,
  TraceDeleteResponse,
  TraceDetail,
  TracePage,
  TraceState,
  UploadResponse,
  Verdict,
} from './types'

/** Backend origin. Overridden per environment; the default matches `npm run app:api`. */
const BASE_URL = import.meta.env?.VITE_API_BASE_URL ?? 'http://localhost:8000'

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

/** List every table stored for one document, in document order. */
export function getTables(documentId: string, signal?: AbortSignal): Promise<TableListResponse> {
  return request<TableListResponse>(url(`/artifacts/${documentId}/tables`), { signal })
}

/** Resolve one table:// link to the stored table. */
export function getTable(
  documentId: string,
  tableId: string,
  signal?: AbortSignal,
): Promise<TableArtifact> {
  return request<TableArtifact>(url(`/artifacts/${documentId}/tables/${tableId}`), {
    signal,
  })
}

/** Delete a file's vectors, leaving the file itself in object storage. */
export function deindexSource(sourceKey: string): Promise<DeindexResponse> {
  return request<DeindexResponse>(url(`/sources/${encodeKey(sourceKey)}/index`), {
    method: 'DELETE',
  })
}

/**
 * Delete a file from object storage along with every vector built from it.
 *
 * The hard counterpart to `deindexSource`, which keeps the file. Deleting a key
 * that is already gone from both sides is not an error — it comes back as
 * zeroes, because the caller asked for a state the store is already in.
 *
 * @throws ApiError - 409 while an indexing run is holding the file.
 */
export function deleteSource(sourceKey: string): Promise<DeleteResponse> {
  return request<DeleteResponse>(url(`/sources/${encodeKey(sourceKey)}`), {
    method: 'DELETE',
  })
}

/**
 * Upload a new file into object storage.
 *
 * The file is stored but not indexed — indexing is a separate, deliberate step.
 * A key that is already taken is refused rather than overwritten.
 *
 * @param file - The file to store.
 * @param prefix - Folder to place it under, if any.
 * @throws ApiError - 400 if the file is unacceptable, 409 if the key is taken.
 */
export function uploadSource(file: File, prefix = ''): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  form.append('prefix', prefix)

  // No Content-Type header: the browser must set it itself so the multipart
  // boundary matches the body it generates.
  return request<UploadResponse>(url('/sources/upload'), { method: 'POST', body: form })
}

/**
 * Replace one file's contents, discarding every vector built from the old ones.
 *
 * The key comes from `sourceKey`, not from the file's own name — a replace
 * targets an existing row, whatever the chosen file happens to be called. The
 * caller should confirm an obvious name mismatch with the user first.
 *
 * @param sourceKey - The object key to overwrite.
 * @param file - The replacement file.
 */
export function replaceSource(sourceKey: string, file: File): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)

  return request<UploadResponse>(url(`/sources/${encodeKey(sourceKey)}`), {
    method: 'PUT',
    body: form,
  })
}

/**
 * Queue files for embedding and return the run they joined.
 *
 * Deliberately not a stream. The work used to be the response, which meant a
 * reload cancelled it mid-file; now this only enqueues, and progress is read
 * from `attachIndexRun`. One worker drains one queue, so calling this while a
 * run is in flight adds to that run rather than starting another.
 *
 * With no `keys`, the run covers everything under `prefix` that needs it.
 */
export function enqueueIndex(
  body: IndexRequest,
  signal?: AbortSignal,
): Promise<EnqueueResponse> {
  return request<EnqueueResponse>(url('/sources/index'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

/**
 * List the run in flight, if any, followed by recent history.
 *
 * Asked on load: a live run carries the `job_id` needed to attach to its
 * stream, which is how progress survives a reload.
 */
export function listIndexRuns(signal?: AbortSignal): Promise<IndexRun[]> {
  return request<IndexRun[]>(url('/sources/index/runs'), { signal })
}

/**
 * Follow one run's events, replaying whatever was missed.
 *
 * @param jobId - The run to follow.
 * @param after - Cursor already seen. The default replays from the start,
 *   which is what a client that just reloaded needs to rebuild its progress.
 * @param signal - Aborting detaches the stream; the run itself keeps going.
 */
export async function* attachIndexRun(
  jobId: string,
  after = -1,
  signal?: AbortSignal,
): AsyncGenerator<IndexEventWithCursor> {
  const target = url(`/sources/index/runs/${encodeURIComponent(jobId)}/events`, {
    after: String(after),
  })

  for await (const raw of getSse(target, signal)) {
    // Every event on this stream is named and carries a JSON payload, plus the
    // cursor in its `id` field so a reconnect can resume from it.
    yield {
      event: { event: raw.event, data: JSON.parse(raw.data) } as IndexEvent,
      cursor: raw.id === '' ? after : Number(raw.id),
    }
  }
}

/**
 * Stop a run, discarding whatever was still queued.
 *
 * Needed because a run no longer dies with the tab that started it.
 */
export function stopIndexRun(jobId: string): Promise<IndexRun> {
  return request<IndexRun>(url(`/sources/index/runs/${encodeURIComponent(jobId)}`), {
    method: 'DELETE',
  })
}

/**
 * List the provider and model pairs this deployment can use.
 *
 * The selector is built from this rather than a hardcoded list, because which
 * providers work depends on the credentials the backend holds.
 */
export function listModels(signal?: AbortSignal): Promise<ModelOption[]> {
  return request<ModelOption[]>(url('/chat/models'), { signal })
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
  for await (const raw of postSse(url('/chat'), body, signal)) {
    if (raw.event === 'message') {
      yield { event: 'message', data: raw.data }
    } else {
      yield { event: raw.event, data: JSON.parse(raw.data) } as ChatEvent
    }
  }
}

// --- Traces and evaluations -------------------------------------------------

/** Filters accepted by the trace listing. */
export interface TraceQuery {
  limit?: number
  offset?: number
  model?: string
  state?: TraceState
  /** True for judged requests only, false for the unjudged backlog. */
  evaluated?: boolean
  verdict?: Verdict
  target?: EvaluationTarget
  /** Only requests that retrieved a chunk from this file. */
  sourceKey?: string
  search?: string
  signal?: AbortSignal
}

/**
 * List recorded chat requests, newest first.
 *
 * Every request is recorded, judged or not — a judgement is made later, by
 * which time the index may have changed, so the evidence has to be captured
 * when the answer was written.
 */
export function listTraces(query: TraceQuery = {}): Promise<TracePage> {
  return request<TracePage>(
    url('/traces', {
      limit: query.limit?.toString(),
      offset: query.offset ? query.offset.toString() : undefined,
      model: query.model,
      state: query.state,
      // Only send `evaluated` when it is actually set: `false` is a filter in
      // its own right, and must not be dropped as falsy.
      evaluated: query.evaluated === undefined ? undefined : String(query.evaluated),
      verdict: query.verdict,
      target: query.target,
      source_key: query.sourceKey,
      search: query.search,
    }),
    { signal: query.signal },
  )
}

/** List every model that has at least one recorded answer. */
export function listTraceModels(signal?: AbortSignal): Promise<string[]> {
  return request<string[]>(url('/traces/models'), { signal })
}

/**
 * Fetch one request with its chunks and every judgement made on it.
 *
 * Unlike the listing, this includes withdrawn judgements — the detail view is
 * where a change of mind is worth seeing.
 */
export function getTrace(traceId: string, signal?: AbortSignal): Promise<TraceDetail> {
  return request<TraceDetail>(url(`/traces/${encodeURIComponent(traceId)}`), { signal })
}

/**
 * Discard a request, its chunks and its judgements.
 *
 * The only hard delete in this API. Withdrawing a judgement keeps the record;
 * this removes the evidence, and is meant for a request that should never have
 * been recorded.
 */
export function deleteTrace(traceId: string): Promise<TraceDeleteResponse> {
  return request<TraceDeleteResponse>(url(`/traces/${encodeURIComponent(traceId)}`), {
    method: 'DELETE',
  })
}

/**
 * Judge one stage of a recorded request.
 *
 * Judging the same stage again adds another verdict rather than replacing the
 * first — the newest live one is what the row shows, and the earlier one stays
 * readable on the trace.
 *
 * @throws ApiError - 404 if the trace has aged out, 400 if a tag does not
 *   belong to the stage being judged.
 */
export function createEvaluation(
  traceId: string,
  body: EvaluationRequest,
): Promise<Evaluation> {
  return request<Evaluation>(url(`/traces/${encodeURIComponent(traceId)}/evaluations`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/**
 * List the verdicts and reason chips the evaluate control should offer.
 *
 * Fetched rather than hardcoded: reason codes a client invents produce
 * judgements nothing can group.
 */
export function listEvaluationOptions(signal?: AbortSignal): Promise<EvaluationOptions> {
  return request<EvaluationOptions>(url('/evaluations/options'), { signal })
}

/** Withdraw a judgement. The record stays, marked withdrawn. */
export function withdrawEvaluation(
  evaluationId: string,
  reason = '',
): Promise<Evaluation> {
  return request<Evaluation>(url(`/evaluations/${encodeURIComponent(evaluationId)}`), {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
}

/** Reinstate a withdrawn judgement. */
export function restoreEvaluation(evaluationId: string): Promise<Evaluation> {
  return request<Evaluation>(
    url(`/evaluations/${encodeURIComponent(evaluationId)}/restore`),
    { method: 'POST' },
  )
}

/**
 * Absolute URL of the JSONL export.
 *
 * Returned rather than fetched because the browser downloads it directly —
 * routing a file through JavaScript would only add a copy in memory.
 */
export function evaluationExportUrl(): string {
  return url('/evaluations/export')
}

// --- Prompts ----------------------------------------------------------------

/**
 * List every prompt the pipeline assembles a request from.
 *
 * Fetched rather than mirrored here for the same reason the evaluation
 * vocabulary is: the templates and the variables they may use are the
 * backend's, and a client editing a placeholder the pipeline cannot fill would
 * only find out when a chat request died mid-stream.
 */
export function listPrompts(signal?: AbortSignal): Promise<Prompt[]> {
  return request<Prompt[]>(url('/prompts'), { signal })
}

/**
 * Replace one prompt's text.
 *
 * Saving the shipped default is treated as a reset, so a prompt stops reading
 * as edited once it matches the code again. Prompts marked `optional` accept an
 * empty template, which turns them off rather than sending a blank message.
 *
 * @throws ApiError - 400 if the template would not render in the pipeline.
 */
export function updatePrompt(promptId: string, template: string): Promise<Prompt> {
  return request<Prompt>(url(`/prompts/${encodeURIComponent(promptId)}`), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template }),
  })
}

/** Restore one prompt to the text it ships with. */
export function resetPrompt(promptId: string): Promise<Prompt> {
  return request<Prompt>(url(`/prompts/${encodeURIComponent(promptId)}/reset`), {
    method: 'POST',
  })
}

/**
 * Render the prompts in force into the exact messages a request would send.
 *
 * The templates read differently apart than assembled — the chunk format is
 * repeated once per retrieved chunk, inside the block that carries them — so
 * the editor shows the result rather than asking anyone to imagine it.
 */
export function previewPrompts(
  body: PromptPreviewRequest = {},
  signal?: AbortSignal,
): Promise<PromptPreview> {
  return request<PromptPreview>(url('/prompts/preview'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

// --- Golden sets ------------------------------------------------------------

/**
 * Start drafting a golden set from a source file.
 *
 * Returns immediately with a job id. Drafting is a dozen model calls, so the
 * work is not the response — follow it with `attachGoldenRun`, which any
 * client can open and reopen.
 *
 * The file does not have to be indexed first. Indexing decides what can be
 * retrieved; a golden set is about what the document says.
 */
export function startGoldenRun(body: GoldenRunRequest): Promise<GoldenEnqueueResponse> {
  return request<GoldenEnqueueResponse>(url('/golden/runs'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/**
 * Follow a generation run's event stream.
 *
 * Each event carries a cursor in its SSE `id`; pass the last one seen back as
 * `after` and the stream resumes there rather than replaying everything. Safe
 * to call on a finished run: it replays and closes, which is how a reload
 * rebuilds the final state.
 */
export async function* attachGoldenRun(
  jobId: string,
  after = -1,
  signal?: AbortSignal,
): AsyncGenerator<GoldenEventWithCursor> {
  const target = url(`/golden/runs/${encodeURIComponent(jobId)}/stream`, {
    after: String(after),
  })

  for await (const raw of getSse(target, signal)) {
    yield {
      event: { event: raw.event, data: JSON.parse(raw.data) } as GoldenEvent,
      cursor: raw.id === '' ? after : Number(raw.id),
    }
  }
}

/** Stop a generation run in flight. */
export function stopGoldenRun(jobId: string): Promise<GoldenRun> {
  return request<GoldenRun>(url(`/golden/runs/${encodeURIComponent(jobId)}`), {
    method: 'DELETE',
  })
}

/** The question types, difficulties and validator checks a set is built from. */
export function getGoldenOptions(signal?: AbortSignal): Promise<GoldenOptions> {
  return request<GoldenOptions>(url('/golden/options'), { signal })
}

/** Every golden set, newest first. */
export function listGoldenSets(signal?: AbortSignal): Promise<GoldenSet[]> {
  return request<GoldenSet[]>(url('/golden/sets'), { signal })
}

/** One golden set with all of its rows and the validator's findings. */
export function getGoldenSet(setId: string, signal?: AbortSignal): Promise<GoldenSetDetail> {
  return request<GoldenSetDetail>(url(`/golden/sets/${encodeURIComponent(setId)}`), { signal })
}

/** Rename the file a set exports as. */
export function renameGoldenSet(setId: string, slug: string): Promise<GoldenSetDetail> {
  return request<GoldenSetDetail>(
    url(`/golden/sets/${encodeURIComponent(setId)}`, { slug }),
    { method: 'PATCH' },
  )
}

/**
 * Edit one row, or record a decision about it.
 *
 * An edit that touches content re-runs every validator check, so fixing a
 * flagged field clears the flag in the same response.
 */
export function updateGoldenRow(
  setId: string,
  rowId: string,
  update: GoldenRowUpdate,
): Promise<GoldenRow> {
  return request<GoldenRow>(
    url(`/golden/sets/${encodeURIComponent(setId)}/rows/${encodeURIComponent(rowId)}`),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    },
  )
}

/** Withdraw a set. Soft, so a run scored against it can still be explained. */
export function deleteGoldenSet(setId: string): Promise<void> {
  return request<void>(url(`/golden/sets/${encodeURIComponent(setId)}`), { method: 'DELETE' })
}

/** Undo a withdrawal. */
export function restoreGoldenSet(setId: string): Promise<GoldenSetDetail> {
  return request<GoldenSetDetail>(
    url(`/golden/sets/${encodeURIComponent(setId)}/restore`),
    { method: 'POST' },
  )
}

/**
 * The URL to download a set as the JSONL the offline harness reads.
 *
 * Returned rather than fetched: the browser should save the file, and routing
 * it through `fetch` only to rebuild a download would lose the filename the
 * server already chose.
 */
export function goldenExportUrl(setId: string): string {
  return url(`/golden/sets/${encodeURIComponent(setId)}/export`)
}

// --- Chunking: previewing, listing and comparing variants -------------------

/**
 * List the ways this deployment can cut a document.
 *
 * The picker is built from this rather than a hardcoded list, so a strategy
 * added on the backend appears without a release on this side.
 */
export function listStrategies(signal?: AbortSignal): Promise<ChunkStrategySpec[]> {
  return request<ChunkStrategySpec[]>(url('/chunking/strategies'), { signal })
}

/**
 * See how a strategy would cut a file. Nothing is embedded and nothing is paid.
 *
 * This is how a strategy gets chosen: look at the shape of the cut first, and
 * only index the ones worth the embedding cost.
 */
export function previewChunking(
  sourceKey: string,
  config: ChunkingConfig,
  signal?: AbortSignal,
): Promise<ChunkPreviewResponse> {
  return request<ChunkPreviewResponse>(url('/chunking/preview'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_key: sourceKey, config }),
    signal,
  })
}

/**
 * List every variant that currently holds vectors.
 *
 * Read back from the index itself, so it is right after a restart and cannot
 * claim a variant somebody deleted from the Pinecone console.
 */
export function listVariants(signal?: AbortSignal): Promise<ChunkVariant[]> {
  return request<ChunkVariant[]>(url('/chunking/variants'), { signal })
}

/**
 * Report which vector space the app answers from.
 *
 * Production is a pointer, not a place — this reads where it currently points
 * and whether that space can still answer.
 */
export function getProduction(signal?: AbortSignal): Promise<ProductionSpace> {
  return request<ProductionSpace>(url('/chunking/production'), { signal })
}

/**
 * Adopt one variant as the space every ungrounded question reads.
 *
 * Instant and reversible: the vectors already exist, so nothing is re-embedded.
 * Refused when the space is empty or holds an incomplete copy of a file.
 */
export function setProduction(variantId: string): Promise<ProductionSpace> {
  return request<ProductionSpace>(url('/chunking/production'), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ variant_id: variantId }),
  })
}

/** Drop a variant and every vector in it. The source files are untouched. */
export function deleteVariant(variantId: string): Promise<VariantDeleteResponse> {
  return request<VariantDeleteResponse>(
    url(`/chunking/variants/${encodeURIComponent(variantId)}`),
    { method: 'DELETE' },
  )
}

/**
 * Start a comparison run: the same golden set put to every variant.
 *
 * Deliberately not a stream. Scoring four variants against twenty questions is
 * minutes of work, so this only starts it; progress is read from
 * `attachVariantScore`, which survives a reload.
 */
export function startVariantScore(
  body: VariantScoreRequest,
  signal?: AbortSignal,
): Promise<ScoreEnqueueResponse> {
  return request<ScoreEnqueueResponse>(url('/chunking/score'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

/**
 * Follow one comparison run, replaying whatever was missed.
 *
 * @param jobId - The run to follow.
 * @param after - Cursor already seen; the default replays from the start.
 * @param signal - Aborting detaches the stream; the run itself keeps going.
 */
export async function* attachVariantScore(
  jobId: string,
  after = -1,
  signal?: AbortSignal,
): AsyncGenerator<ScoreEventWithCursor> {
  const target = url(`/chunking/score/${encodeURIComponent(jobId)}/events`, {
    after: String(after),
  })

  for await (const raw of getSse(target, signal)) {
    yield {
      event: { event: raw.event, data: JSON.parse(raw.data) } as ScoreEvent,
      cursor: raw.id === '' ? after : Number(raw.id),
    }
  }
}

/** Stop a comparison run, keeping whatever it already measured. */
export function stopVariantScore(jobId: string): Promise<ScoreRun> {
  return request<ScoreRun>(url(`/chunking/score/${encodeURIComponent(jobId)}`), {
    method: 'DELETE',
  })
}
