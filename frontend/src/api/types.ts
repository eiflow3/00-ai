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
  /** ISO timestamp of when the vectors were written. */
  embedded_at: string | null
  /** The object's last-modified time as it was at embedding time. */
  source_last_modified: string | null
  /** The object's content hash as it was at embedding time. */
  source_etag: string
  embedding_model: string
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
}

/** One indexed chunk, as stored in the vector index. */
export interface SourceChunk {
  vector_id: string
  chunk_index: number
  content: string
  char_count: number
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
  /** Vectors discarded because the content they described was replaced. */
  pruned: number
}

/** Result of removing a file's vectors. */
export interface DeindexResponse {
  source_key: string
  deleted: number
}

// --- Index run: POST /sources/index stream ----------------------------------

/** Request body for running the data embedding pipeline. */
export interface IndexRequest {
  /** Specific keys to index. Empty means "everything under prefix that needs it". */
  keys?: string[]
  prefix?: string
  only_stale?: boolean
  force?: boolean
}

export type IndexStage = 'loading' | 'chunking' | 'embedding' | 'upserting'

export interface IndexStartedEventData {
  keys: string[]
  total: number
  embedding_model: string
}

export interface IndexProgressEventData {
  source_key: string
  stage: IndexStage
  file_number: number
  total_files: number
  chunk_count: number
}

export interface IndexCompletedEventData {
  source_key: string
  chunk_count: number
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
  total_pruned: number
  /** Each processed file's state, re-read from both sides after the run. */
  statuses: SourceStatus[]
}

/** Every event the indexing stream can emit, discriminated by `event`. */
export type IndexEvent =
  | { event: 'started'; data: IndexStartedEventData }
  | { event: 'progress'; data: IndexProgressEventData }
  | { event: 'completed'; data: IndexCompletedEventData }
  | { event: 'error'; data: IndexErrorEventData }
  | { event: 'summary'; data: IndexSummaryEventData }

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

export interface ChatRequest {
  query: string
  provider?: 'openai' | 'claude'
  model?: string
  system_prompt?: string
  temperature?: number
  use_rag?: boolean
  top_k?: number
  score_threshold?: number
}

/**
 * Every event the chat stream can emit.
 *
 * Text deltas arrive with no `event:` line, so they surface as SSE's default
 * `message` type — that is what distinguishes answer text from metadata.
 */
export type ChatEvent =
  | { event: 'message'; data: string }
  | { event: 'retrieval'; data: RetrievalEventData }
  | { event: 'error'; data: ChatErrorEventData }
  | { event: 'usage'; data: UsageEventData }
