/**
 * Types mirroring the backend's pydantic schemas.
 *
 * Kept in one file so a backend schema change has a single place to land on
 * this side. Names match `backend/app/schemas/` exactly.
 */

// --- Sources: the data embedding pipeline -----------------------------------

/**
 * Where a source file stands relative to its embeddings.
 *
 * This is the verdict the backend computes by comparing object storage against
 * the vector index. The UI renders it; it never re-derives it.
 */
export type IndexState =
  | 'not_indexed'
  | 'current'
  | 'stale_content'
  | 'stale_model'
  | 'interrupted'
  | 'orphaned'
  | 'unsupported'

/** A file as it exists in object storage — the R2 side. */
export interface SourceObject {
  key: string
  /** ISO timestamp of the object's last write in storage. */
  last_modified: string
  size: number
  /** Content hash. Changes whenever the bytes change. */
  etag: string
}

/**
 * What the vector index holds for one file — the Pinecone side.
 *
 * Every field is a snapshot taken when the file was embedded, which is what
 * makes staleness detectable at all.
 */
export interface IndexedDocument {
  /** The object key these vectors came from — the join key back to storage. */
  source_key: string
  /** Stable id derived from the source key; prefixes every vector id. */
  document_id: string
  chunk_count: number
  /**
   * How many chunks the last run said the file should have.
   *
   * Compared against `chunk_count` by the backend to detect a run that stopped
   * partway. Zero on vectors written before this was recorded.
   */
  chunk_total: number
  /** ISO timestamp of when the vectors were written. */
  embedded_at: string | null
  /** The object's last-modified time as it was at embedding time. */
  source_last_modified: string | null
  /** The object's content hash as it was at embedding time. */
  source_etag: string
  embedding_model: string
}

/**
 * One vector space holding a copy of a file.
 *
 * A file can be cut several ways at once, each cut in its own namespace, so
 * "is this indexed" has one answer per space. Each copy is judged on its own
 * terms — one embedded before the file changed reads stale while another,
 * re-cut afterwards, reads current.
 */
export interface SourceVariant {
  /** Empty is the original production index. */
  variant_id: string
  label: string
  state: IndexState
  chunk_count: number
  embedded_at: string | null
  /** True when this is the space the app currently answers from. */
  active: boolean
}

/** One file joined with its embeddings — a row in the sources table. */
export interface SourceStatus {
  source_key: string
  state: IndexState
  /** Absent when the file has been deleted but its vectors remain. */
  source: SourceObject | null
  /** Absent when nothing has been embedded from this file. */
  indexed: IndexedDocument | null
  /** Plain-language reason for the verdict, written by the backend. */
  detail: string
  /**
   * Whether a run is embedding this file right now.
   *
   * Orthogonal to `state`, which describes what is stored — a file reads
   * `not_indexed` while its very first embeddings are still being built.
   */
  indexing: boolean
  /**
   * Whether the file is waiting its turn.
   *
   * One worker drains the queue, so a file is accepted long before anything
   * starts happening to it. Showing "Indexing" during that wait would be a lie.
   */
  queued: boolean
  /**
   * Every vector space holding a copy of this file, newest copy first.
   *
   * Read back from the indexes rather than remembered, so a namespace deleted
   * on a console stops appearing on the next request.
   */
  variants: SourceVariant[]
}

/** One indexed chunk, as stored in the vector index. */
export interface SourceChunk {
  vector_id: string
  chunk_index: number
  content: string
  char_count: number
  /** First and last source page the chunk touches. Absent for pageless formats. */
  page_start?: number | null
  page_end?: number | null
}

/** One file in full — its status plus every chunk indexed from it. */
export interface SourceDetail {
  status: SourceStatus
  chunks: SourceChunk[]
}

/**
 * Result of writing a file into object storage.
 *
 * Both upload and replace return this shape. The status is included because a
 * write changes which side of the pipeline knows about the file — the caller
 * does not have to re-list to find out.
 */
export interface UploadResponse {
  /** The file's state after the write; `not_indexed` in both cases. */
  status: SourceStatus
  /** Whether this overwrote an existing file rather than creating one. */
  replaced: boolean
  /** False when identical content was already stored, making the upload a no-op. */
  created: boolean
  /** Vectors discarded because the content they described was replaced. */
  pruned: number
}

/** Result of removing a file's vectors. */
export interface DeindexResponse {
  source_key: string
  deleted: number
}

/**
 * Result of deleting a file and its embeddings.
 *
 * Both sides are reported because they can legitimately disagree: a file that
 * was never indexed deletes with no vectors, an orphan deletes with no file,
 * and a key already gone from both comes back all zeroes rather than failing.
 */
export interface DeleteResponse {
  source_key: string
  vectors_deleted: number
  file_deleted: boolean
}

// --- Artifacts: stored by-products of extraction ------------------------------

/** One table lifted out of a document at extraction time, stored verbatim. */
export interface ExtractedTable {
  /** Sequential within the document: "table-001", "table-002" … */
  table_id: string
  /** The table itself, as a markdown pipe table. */
  markdown: string
  /** Page holding the table, when the format knows pages. */
  page?: number | null
  /** The caption the document gave it, when one was found. */
  caption?: string | null
}

/** Every table one document currently has stored. */
export interface TableListResponse {
  document_id: string
  tables: ExtractedTable[]
}

/** One stored table, resolved from a table:// link. */
export interface TableArtifact {
  document_id: string
  table_id: string
  markdown: string
  page?: number | null
  caption?: string | null
}

// --- Index runs: enqueue, attach, stop --------------------------------------

/** Request body for queueing files for embedding. */
export interface IndexRequest {
  /** Specific keys to index. Empty means "everything under prefix that needs it". */
  keys?: string[]
  prefix?: string
  only_stale?: boolean
  force?: boolean
  /** How to cut the text. Ignored when `variant` is set. */
  strategy?: ChunkStrategy
  chunk_size?: number
  chunk_overlap?: number
  /**
   * Variant to embed into, e.g. `recursive-512-64`.
   *
   * Empty indexes into the production index the app answers from. A variant's
   * name fully determines how it is cut, so the three fields above are ignored
   * when one is given.
   */
  variant?: string
}

/** Where a run stands. Every state but `running` is terminal. */
export type IndexRunState =
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  /** The server stopped while this run was in flight. */
  | 'abandoned'

/**
 * One indexing run, live or finished.
 *
 * Asked for on load: a run in flight carries the `job_id` needed to attach to
 * its stream, which is how progress survives a reload.
 */
export interface IndexRun {
  job_id: string
  state: IndexRunState
  /** Keys still waiting, in queue order. */
  pending: string[]
  /** Key being embedded right now, empty when none is. */
  current: string
  /** Files this run has taken on. Grows when a later request joins the run. */
  total: number
  indexed: number
  skipped: number
  failed: number
  /** Chunks the run did not have to embed. */
  total_reused: number
  started_at: string | null
  finished_at: string | null
  error: string
  /** Cursor of the last event emitted; pass as `after` to resume a stream. */
  last_cursor: number
}

/** Result of asking for files to be indexed. The request only enqueues. */
export interface EnqueueResponse {
  /** The run the accepted files joined — new, or one already in flight. */
  job_id: string
  accepted: string[]
  /** Already queued or being embedded. Not an error, just redundant. */
  already_queued: string[]
  /** Refused because the queue is full. */
  rejected: string[]
  /** Named keys with no object behind them. */
  missing: string[]
  /** The configured ceiling, so a rejection can be explained without hardcoding it. */
  limit: number
  pending: string[]
}

export type IndexStage =
  | 'loading'
  | 'extracting'
  | 'describing_tables'
  | 'chunking'
  | 'embedding'
  | 'upserting'

export interface IndexStartedEventData {
  /** The run this stream reports on. Kept so a reload can ask for it again. */
  job_id: string
  keys: string[]
  total: number
  embedding_model: string
}

/** Sent when a later request joins a run already in flight. */
export interface IndexQueuedEventData {
  added: string[]
  pending: string[]
  /** Files the run has taken on in total — the denominator for a progress bar. */
  total: number
}

export interface IndexProgressEventData {
  source_key: string
  stage: IndexStage
  file_number: number
  /** Can grow mid-run, so read it from the latest event rather than caching it. */
  total_files: number
  chunk_count: number
}

export interface IndexCompletedEventData {
  source_key: string
  chunk_count: number
  /**
   * Chunks that did not need re-embedding, because the index already held them
   * identically. Non-zero means an interrupted run was resumed, not repeated.
   */
  reused: number
  pruned: number
  skipped: boolean
  state: IndexState
}

export interface IndexErrorEventData {
  source_key: string
  stage: string
  message: string
}

export interface IndexSummaryEventData {
  indexed: number
  skipped: number
  failed: number
  total_chunks: number
  /** Chunks the run did not have to embed — the measure of what resuming saved. */
  total_reused: number
  total_pruned: number
  /** Each processed file's state, re-read from both sides after the run. */
  statuses: SourceStatus[]
}

/** Every event the indexing stream can emit, discriminated by `event`. */
export type IndexEvent =
  | { event: 'started'; data: IndexStartedEventData }
  | { event: 'queued'; data: IndexQueuedEventData }
  | { event: 'progress'; data: IndexProgressEventData }
  | { event: 'completed'; data: IndexCompletedEventData }
  | { event: 'error'; data: IndexErrorEventData }
  | { event: 'summary'; data: IndexSummaryEventData }

/** An event with the cursor it arrived under, so a reconnect can resume. */
export interface IndexEventWithCursor {
  event: IndexEvent
  cursor: number
}

// --- Chat: POST /chat stream ------------------------------------------------

/** A chunk returned by the similarity search, with its score. */
export interface RetrievedChunk {
  chunk_id: string
  document_id: string
  content: string
  /** Similarity score between query and chunk, 0-1. */
  score: number
  /** The object key this chunk came from. */
  source: string
}

export interface RetrievalEventData {
  query: string
  chunks: RetrievedChunk[]
  total_searched: number
  embedding_model: string
}

export interface ChatErrorEventData {
  stage: string
  message: string
}

/** Whether a pipeline stage is running, finished, or failed. */
export type StageStatus = 'started' | 'completed' | 'failed'

/**
 * One step of the request's pipeline, reported as it starts and again as it ends.
 *
 * The set of stages is the server's to decide and will grow, so nothing here
 * lists them: `label` is display wording sent by the stage itself, and a step
 * added to the pipeline later renders without a change on this side.
 */
export interface StageEventData {
  /** Position in the timeline. The start and end of one stage share it. */
  sequence: number
  /** Stable id for the stage, safe to branch on. */
  name: string
  /** Human wording, ready to display as-is. */
  label: string
  status: StageStatus
  /** Duration in milliseconds; 0 while the stage is still running. */
  elapsed_ms: number
  /** What the stage produced, or why it failed. */
  detail: string
}

/**
 * Token counts and cost for the generation call.
 *
 * Mirrors `CostBreakdown.to_dict()`. The embedding call made during retrieval
 * is not priced into this — it covers generation only.
 */
export interface UsageEventData {
  model: string
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  input_cost: number
  output_cost: number
  cache_read_cost: number
  cache_write_cost: number
  total_cost: number
}

/**
 * One provider/model pair this deployment offers.
 *
 * The selector is built from these rather than a hardcoded list: which
 * providers work depends on the credentials the backend has.
 */
export interface ModelOption {
  provider: 'openai' | 'claude'
  provider_label: string
  model: string
  model_label: string
  /** False when this deployment cannot use it at all. */
  available: boolean
  /** A caveat worth showing even when the option is usable. */
  detail: string
  /** False when the model answers but reports zero cost. */
  priced: boolean
}

export interface ChatRequest {
  query: string
  provider?: 'openai' | 'claude'
  model?: string
  system_prompt?: string
  temperature?: number
  use_rag?: boolean
  top_k?: number
  score_threshold?: number
  /**
   * Which chunking variant to answer from. Empty is the production index.
   *
   * The whole basis of the comparison: hold the question, the model, the
   * prompt and `top_k` still, change only this, and the difference in the
   * answer is the difference the chunking made.
   */
  chunk_variant?: string
}

/**
 * Every event the chat stream can emit.
 *
 * Text deltas arrive with no `event:` line, so they surface as SSE's default
 * `message` type — that is what distinguishes answer text from metadata.
 *
 * `trace` arrives first, before retrieval runs, carrying the id this request is
 * being recorded under. It is what an evaluation is later filed against.
 *
 * `stage` events run alongside everything else, reporting each pipeline step as
 * it starts and ends.
 */
export type ChatEvent =
  | { event: 'trace'; data: TraceEventData }
  | { event: 'message'; data: string }
  | { event: 'stage'; data: StageEventData }
  | { event: 'retrieval'; data: RetrievalEventData }
  | { event: 'error'; data: ChatErrorEventData }
  | { event: 'usage'; data: UsageEventData }

// --- Traces: the recorded evidence behind each answer -----------------------

/** How a chat request ended. */
export type TraceState = 'completed' | 'failed' | 'cancelled'

/**
 * One chunk as it was at answer time.
 *
 * The text is stored, not referenced. Chunk ids are positional, so a re-index
 * at a different chunk size leaves the same id pointing at different text —
 * a trace that kept only ids would quietly start describing a different answer.
 */
export interface TraceChunk {
  /** Position in the ranked results, best first. */
  rank: number
  chunk_id: string
  document_id: string
  source_key: string
  score: number
  content: string
  /** Fingerprint of the text, so a later re-index can be told it changed. */
  content_hash: string
  char_count: number
  /** True when the score threshold kept this chunk out of the prompt. */
  dropped: boolean
}

/** One recorded chat request, without its chunks or judgements. */
export interface Trace {
  trace_id: string
  created_at: string
  question: string
  answer: string

  provider: string
  model: string
  temperature: number
  system_prompt: string

  use_rag: boolean
  top_k: number
  score_threshold: number
  embedding_model: string
  total_searched: number
  /** Chunks that reached the prompt — dropped ones are not counted here. */
  chunk_count: number
  top_score: number

  state: TraceState
  error_stage: string
  error_message: string

  retrieval_ms: number
  generation_ms: number
  total_ms: number
  input_tokens: number
  output_tokens: number
  total_cost: number

  /** Live judgements on this trace. */
  evaluation_count: number
  /** Latest live verdict per target, e.g. `{ retrieval: 'good' }`. */
  verdicts: Partial<Record<EvaluationTarget, Verdict>>
}

/** One trace with everything attached — including withdrawn judgements. */
export interface TraceDetail {
  trace: Trace
  chunks: TraceChunk[]
  evaluations: Evaluation[]
}

export interface TracePage {
  traces: Trace[]
  total: number
  limit: number
  offset: number
}

export interface TraceDeleteResponse {
  trace_id: string
  deleted: boolean
}

// --- Evaluations: the judgements made on those requests ---------------------

/**
 * Which stage a judgement is about.
 *
 * This is what turns "the answer was wrong" into something actionable — the
 * whole reason the chunks are kept alongside the answer.
 */
export type EvaluationTarget = 'retrieval' | 'generation' | 'overall'

export type Verdict = 'good' | 'partial' | 'bad'

/** Who judged it. Recorded so a machine judge never reads as a human one. */
export type EvaluationAuthor = 'human' | 'llm' | 'code'

export interface Evaluation {
  id: string
  trace_id: string
  target: EvaluationTarget
  verdict: Verdict
  /** Preset reason ids, all belonging to `target`. */
  tags: string[]
  note: string
  author: EvaluationAuthor
  created_at: string
  /**
   * Whether the judgement has been withdrawn.
   *
   * Withdrawn judgements are kept: the evidence is still evidence, and a
   * change of mind is itself worth reading.
   */
  deleted: boolean
  deleted_at: string | null
  deleted_reason: string
}

/** Request body for judging one stage of a trace. */
export interface EvaluationRequest {
  target: EvaluationTarget
  verdict: Verdict
  tags?: string[]
  note?: string
}

export interface VerdictOption {
  id: Verdict
  label: string
  hint: string
}

/** One reason chip, scoped to the stage it can explain. */
export interface TagOption {
  id: string
  label: string
  target: EvaluationTarget
  hint: string
}

/**
 * The vocabulary an evaluation is written in.
 *
 * Served by the backend rather than hardcoded here for the same reason the
 * model list is: reason codes each client invents cannot be counted.
 */
export interface EvaluationOptions {
  verdicts: VerdictOption[]
  tags: TagOption[]
  targets: EvaluationTarget[]
}

export interface EvaluationPage {
  evaluations: Evaluation[]
  total: number
  limit: number
  offset: number
}

/** Payload of the chat stream's first event. */
export interface TraceEventData {
  trace_id: string
}

// --- Prompts ----------------------------------------------------------------

/** The prompts the pipeline assembles a request from. */
export type PromptId =
  | 'system'
  | 'context_block'
  | 'chunk_format'
  | 'no_context'
  | 'golden_section'
  | 'golden_cross_section'
  | 'golden_unanswerable'

/**
 * Which part of the system a prompt steers.
 *
 * Answering a question and drafting an evaluation set are different jobs, so
 * the editor sections the list rather than running them together.
 */
export type PromptGroup = 'chat' | 'golden'

/** One value a template may interpolate, and whether it must. */
export interface PromptVariable {
  /** Placeholder name, written in the template as {name}. */
  name: string
  description: string
  /** True when a template that leaves it out would drop information. */
  required: boolean
  /** Stand-in value used to preview the template. */
  example: string
}

/**
 * One prompt as it currently stands, with the default it was built from.
 *
 * `template` is what the pipeline will use; `default_template` is what ships
 * with the code. They differ exactly while `edited` is true, which is what a
 * reset undoes.
 */
export interface Prompt {
  id: PromptId
  group: PromptGroup
  label: string
  description: string
  /** When the pipeline uses this template, written for a person. */
  applies_when: string
  template: string
  default_template: string
  variables: PromptVariable[]
  edited: boolean
  updated_at: string | null
  /** True when an empty template is allowed, and turns the prompt off. */
  optional: boolean
}

/** Request body for saving an override. */
export interface PromptUpdateRequest {
  template: string
}

/** One assembled message, as the provider adapters receive it. */
export interface PromptMessage {
  role: string
  content: string
}

/** Request body for rendering the prompts into the messages they produce. */
export interface PromptPreviewRequest {
  query?: string
  chunk_count?: number
  /** False previews the path taken when RAG is off. */
  grounded?: boolean
}

/** The message list the prompts currently in force would produce. */
export interface PromptPreview {
  messages: PromptMessage[]
  character_count: number
}

// --- Golden sets ------------------------------------------------------------

/** What a golden question is testing. */
export type GoldenQuestionType =
  | 'lookup'
  | 'temporal'
  | 'distractor'
  | 'multi_hop'
  | 'arithmetic'
  | 'synthesis'
  | 'unanswerable'

export type GoldenDifficulty = 'easy' | 'medium' | 'hard'

/** Whether the validator could ground a row in the source document. */
export type GoldenRowStatus = 'valid' | 'flagged'

/** What a person decided about a row. */
export type GoldenReview = 'pending' | 'accepted' | 'dropped'

export type GoldenSetState = 'drafting' | 'ready' | 'failed'

/** The stages a generation run moves through, in order. */
export type GoldenStage =
  | 'extract'
  | 'segment'
  | 'facts'
  | 'draft'
  | 'validate'
  | 'self_check'

/** One validator check a row did not pass. */
export interface GoldenIssue {
  /** Check name, e.g. 'keys_verbatim'. */
  check: string
  /** What was wrong, naming the offending value. */
  detail: string
}

/**
 * How a computed figure was derived from figures the document states.
 *
 * Shown so a reviewer can see the working, and recomputed by the validator so
 * working that does not add up is rejected. Never exported.
 */
export interface GoldenDerivation {
  operands: number[]
  operator: string
  explanation: string
}

/**
 * One question, its reference answer, and how to score an attempt at it.
 *
 * The exported subset is fixed by the offline harness. The rest — the
 * derivation, the validator's findings, the review decision — is how the row
 * got here, and stops at the export boundary.
 */
export interface GoldenRow {
  /** Internal id. Edits address this, never the exported Q-number. */
  row_id: string
  /** The id the harness reads. Blank on a dropped row, which exports nothing. */
  question_id: string
  type: GoldenQuestionType
  difficulty: GoldenDifficulty
  question: string
  answer: string
  numeric_answer: number | null
  numeric_tolerance: number | null
  /** Strings an answer must contain, verbatim from the source. */
  answer_keys: string[]
  /** Strings that fail the row if present — the distractor's trap. */
  forbidden_keys: string[]
  /** Whether the answer must decline to state this. */
  must_refuse: boolean
  gold_sections: string[]
  note: string
  derivation: GoldenDerivation | null
  status: GoldenRowStatus
  issues: GoldenIssue[]
  review: GoldenReview
  edited: boolean
}

/** A generated answer key for one source file. */
export interface GoldenSet {
  set_id: string
  source_key: string
  /** Filename stem used on export. */
  slug: string
  state: GoldenSetState
  provider: string
  model: string
  created_at: string | null
  updated_at: string | null
  row_count: number
  valid_count: number
  accepted_count: number
  /** Section titles a row may cite, so an edit picks from the real outline. */
  sections: string[]
  error: string
  deleted: boolean
}

export interface GoldenSetDetail extends GoldenSet {
  rows: GoldenRow[]
}

/** Request body for starting a generation run. */
export interface GoldenRunRequest {
  source_key: string
  slug?: string
  provider?: string
  model?: string
  /** Multiplier on the per-section quota. The quota itself comes from the document. */
  density?: number
}

/**
 * Body for editing or judging one row.
 *
 * Every field optional: a review decision and a text edit go through the same
 * endpoint, and omitting a field leaves it alone.
 */
export interface GoldenRowUpdate {
  type?: GoldenQuestionType
  difficulty?: GoldenDifficulty
  question?: string
  answer?: string
  numeric_answer?: number | null
  numeric_tolerance?: number | null
  answer_keys?: string[]
  forbidden_keys?: string[]
  must_refuse?: boolean
  gold_sections?: string[]
  note?: string
  review?: GoldenReview
}

/** The vocabulary a client may display or filter by. */
export interface GoldenOptions {
  types: string[]
  difficulties: string[]
  checks: string[]
}

export type GoldenRunState = 'running' | 'completed' | 'failed' | 'abandoned'

/** A generation run, as a client reopening the stream finds it. */
export interface GoldenRun {
  job_id: string
  set_id: string
  source_key: string
  state: GoldenRunState
  stage: GoldenStage | null
  /** Sections drafted so far. */
  completed: number
  total: number
  row_count: number
  started_at: string | null
  finished_at: string | null
  error: string
  /** Highest cursor emitted, so a reconnect resumes from it. */
  last_cursor: number
}

export interface GoldenEnqueueResponse {
  job_id: string
  set_id: string
}

// --- Golden sets: GET /golden/runs/{job_id}/stream --------------------------

export interface GoldenStartedEventData {
  job_id: string
  set_id: string
  source_key: string
  model: string
}

export interface GoldenStageEventData {
  stage: GoldenStage
  /** What it did, e.g. the section drafted. */
  detail: string
  completed: number
  total: number
}

export interface GoldenRowEventData {
  row: GoldenRow
}

export interface GoldenErrorEventData {
  stage: GoldenStage
  detail: string
  message: string
  /** False for a failed section: the run reports it and carries on. */
  fatal: boolean
}

export interface GoldenSummaryEventData {
  set_id: string
  slug: string
  row_count: number
  valid_count: number
  flagged_count: number
  by_type: Record<string, number>
  elapsed_ms: number
  total_cost: number
}

/** Every event the generation stream can emit, discriminated by `event`. */
export type GoldenEvent =
  | { event: 'started'; data: GoldenStartedEventData }
  | { event: 'stage'; data: GoldenStageEventData }
  | { event: 'row'; data: GoldenRowEventData }
  | { event: 'error'; data: GoldenErrorEventData }
  | { event: 'summary'; data: GoldenSummaryEventData }

/** An event with the cursor it arrived under, so a reconnect can resume. */
export interface GoldenEventWithCursor {
  event: GoldenEvent
  cursor: number
}

// --- Chunking: comparing ways to cut a document -----------------------------

/**
 * How a document's text is cut into embeddable segments.
 *
 * Mirrors the backend enum. The picker is built from `listStrategies`, not from
 * this type — a strategy added on the backend appears without a change here.
 */
export type ChunkStrategy = 'boundary' | 'fixed' | 'recursive' | 'structural'

/** A strategy and the geometry it runs at. Together they identify a variant. */
export interface ChunkingConfig {
  strategy: ChunkStrategy
  chunk_size: number
  chunk_overlap: number
}

/** One strategy on offer, described for a person choosing between them. */
export interface ChunkStrategySpec {
  id: ChunkStrategy
  label: string
  /** One line on what it does to a document. */
  summary: string
  /** Where it tends to win or lose. Shown on request, not in the list. */
  detail: string
  /** False when the overlap control does nothing for this strategy. */
  honours_overlap: boolean
  /** True when cutting itself calls a paid API, beyond the embeddings. */
  costs_api_calls: boolean
}

/** One chunk as a preview shows it, before anything is embedded. */
export interface PreviewChunk {
  chunk_index: number
  content: string
  token_count: number
  char_count: number
  start_offset: number
  end_offset: number
  /** Why the chunk ends where it does — the heading it sits under, say. */
  note: string
}

/**
 * The shape of a whole cut.
 *
 * What a preview is really for: two strategies at the same nominal size can
 * produce twenty-four chunks and eleven, and that decides more about retrieval
 * than anything visible in any one chunk.
 */
export interface ChunkPreviewStats {
  chunk_count: number
  total_tokens: number
  document_tokens: number
  min_tokens: number
  median_tokens: number
  max_tokens: number
  /** Share of embedded tokens that repeat a neighbouring chunk. */
  repeated_fraction: number
}

export interface ChunkPreviewResponse {
  source_key: string
  /** The variant this configuration would create if it were indexed. */
  variant_id: string
  label: string
  config: ChunkingConfig
  stats: ChunkPreviewStats
  chunks: PreviewChunk[]
}

/** Whether a variant holds every vector its last run said it should. */
export type VariantState = 'ready' | 'interrupted' | 'missing'

/** One strategy-and-geometry combination that has been embedded. */
export interface ChunkVariant {
  variant_id: string
  label: string
  config: ChunkingConfig
  embedding_model: string
  source_keys: string[]
  vector_count: number
  /** What the last run said it should hold. Disagreement means interrupted. */
  chunk_total: number
  state: VariantState
  embedded_at: string | null
}

export interface VariantDeleteResponse {
  variant_id: string
  deleted: number
}

/**
 * Where the app answers questions from.
 *
 * Production is a pointer rather than a place: one stored variant id naming the
 * namespace `/chat` reads when a request names none itself. Moving it adopts a
 * different way of cutting the documents with nothing re-embedded.
 */
export interface ProductionSpace {
  /** Empty is the original production index. */
  variant_id: string
  label: string
  /** `missing` means the namespace it names holds no vectors any more. */
  state: VariantState
  vector_count: number
  source_keys: string[]
  updated_at: string | null
  /**
   * What the original production index still holds.
   *
   * Zero means it has been retired, and there is nowhere to go back to — which
   * is what decides whether "back to the original index" is offered at all.
   */
  original_vector_count: number
}

// --- Chunking: scoring one variant against a golden set ---------------------

export interface VariantScoreRequest {
  set_id: string
  /** Empty means every variant holding the set's source file. */
  variants?: string[]
  top_k?: number
  /** False measures retrieval only, at no model cost. */
  generate?: boolean
  provider?: string
  model?: string
}

/** How one variant did on one question. */
export interface RowScore {
  question_id: string
  question: string
  /** Null when the run did not generate answers. */
  correct: boolean | null
  recall: number | null
  precision: number | null
  top_score: number
  gold_sections: string[]
  retrieved_sections: string[]
  answer: string
  reasons: string[]
  error: string
}

/** One variant's result across the whole golden set. */
export interface VariantScore {
  variant_id: string
  label: string
  config: ChunkingConfig | null
  rows: number
  correct: number
  recall: number
  precision: number
  failed: number
  duration_seconds: number
  scores: RowScore[]
}

export interface ScoreStartedEventData {
  job_id: string
  set_id: string
  source_key: string
  variants: string[]
  rows: number
  generating: boolean
}

export interface ScoreProgressEventData {
  variant_id: string
  completed: number
  total: number
  score: RowScore
}

export interface ScoreErrorEventData {
  variant_id: string
  question_id: string
  message: string
}

export interface ScoreSummaryEventData {
  /** Every variant, ranked by retrieval recall. */
  scores: VariantScore[]
  winner: string
  duration_seconds: number
}

/** Every event the scoring stream can emit. */
export type ScoreEvent =
  | { event: 'started'; data: ScoreStartedEventData }
  | { event: 'progress'; data: ScoreProgressEventData }
  | { event: 'variant'; data: VariantScore }
  | { event: 'error'; data: ScoreErrorEventData }
  | { event: 'summary'; data: ScoreSummaryEventData }

/** One scoring event with the cursor to resume from. */
export interface ScoreEventWithCursor {
  event: ScoreEvent
  cursor: number
}

export interface ScoreEnqueueResponse {
  job_id: string
  variants: string[]
  rows: number
}

export type ScoreRunState = 'running' | 'completed' | 'failed' | 'cancelled'

export interface ScoreRun {
  job_id: string
  set_id: string
  state: ScoreRunState
  variants: string[]
  completed: number
  total: number
  started_at: string | null
  finished_at: string | null
  error: string
  last_cursor: number
}
