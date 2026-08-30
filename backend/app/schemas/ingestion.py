"""Data embedding pipeline — request body and streamed event payloads.

Indexing a batch of files runs long enough that a single JSON response would
sit behind a proxy timeout, so the endpoint streams its progress instead. Every
event it can emit is defined here, and documented in app.docs.sources.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.source import IndexState, SourceStatus
from app.services.chunker import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE
from app.services.embeddings import DEFAULT_EMBEDDING_MODEL


class IndexRequest(BaseModel):
    """Request body for running the data embedding pipeline.

    With no `keys`, the run covers everything under `prefix` that needs it —
    which is the re-index-what-is-stale case. Naming keys explicitly targets
    just those files.
    """

    # Specific files to index. Empty means "decide from the bucket listing".
    keys: list[str] = Field(
        default_factory=list,
        description="Object keys to index. Empty means every file under `prefix`.",
    )

    # Restrict a bucket-wide run to one folder.
    prefix: str = Field(
        default="",
        description="Restrict the run to keys beginning with this prefix",
    )

    # Whether to skip files whose embeddings already match storage.
    only_stale: bool = Field(
        default=True,
        description="Skip files whose embeddings are already up to date",
    )

    # Re-embed even when nothing changed.
    force: bool = Field(
        default=False,
        description="Re-embed every selected file, even if it is already current",
    )

    # Chunk sizing, in tokens.
    chunk_size: int = Field(
        default=DEFAULT_CHUNK_SIZE,
        ge=64,
        le=8000,
        description="Maximum tokens per chunk",
    )

    # Tokens repeated between consecutive chunks.
    chunk_overlap: int = Field(
        default=DEFAULT_CHUNK_OVERLAP,
        ge=0,
        le=4000,
        description="Tokens repeated between consecutive chunks",
    )

    # Model used to embed the chunks. Must match what queries will use.
    embedding_model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL,
        description="Embedding model used to embed the chunks",
    )


class IndexStartedEventData(BaseModel):
    """Payload of the `started` event, sent once before any file is processed."""

    # The files this run will process, in the order they will be handled.
    keys: list[str] = Field(
        default_factory=list, description="Object keys this run will process"
    )

    # How many files that is.
    total: int = Field(default=0, ge=0, description="Number of files to process")

    # Model the run will embed with.
    embedding_model: str = Field(
        default="", description="Embedding model used for this run"
    )


class IndexStartedEvent(BaseModel):
    """Opens the stream with the run's scope."""

    event: Literal["started"] = Field(default="started", description="The SSE event name")
    data: IndexStartedEventData = Field(..., description="What this run will process")


class IndexProgressEventData(BaseModel):
    """Payload of the `progress` event, sent as each stage of a file completes."""

    # Which file this concerns.
    source_key: str = Field(..., description="Object key being processed")

    # Which stage just finished: loading, chunking, embedding or upserting.
    stage: Literal["loading", "chunking", "embedding", "upserting"] = Field(
        ..., description="Pipeline stage that just completed for this file"
    )

    # Position of this file in the run, 1-based.
    file_number: int = Field(default=0, ge=0, description="1-based position in the run")

    # Total files in the run, so a client can render a bar without counting.
    total_files: int = Field(default=0, ge=0, description="Total files in this run")

    # Chunks produced so far, once chunking has happened.
    chunk_count: int = Field(default=0, ge=0, description="Chunks produced for this file")


class IndexProgressEvent(BaseModel):
    """Reports one stage of one file finishing."""

    event: Literal["progress"] = Field(default="progress", description="The SSE event name")
    data: IndexProgressEventData = Field(..., description="Stage that just completed")


class IndexCompletedEventData(BaseModel):
    """Payload of the `completed` event, sent once per file that finished."""

    # Which file finished.
    source_key: str = Field(..., description="Object key that finished")

    # How many chunks were written.
    chunk_count: int = Field(default=0, ge=0, description="Chunks written to the index")

    # Leftover chunks removed because the file shrank.
    pruned: int = Field(default=0, ge=0, description="Stale chunks removed")

    # Whether the file was actually embedded, or skipped as already current.
    skipped: bool = Field(
        default=False, description="True when the file was already up to date"
    )

    # The file's state after the run — `current` on success.
    state: IndexState = Field(
        default=IndexState.CURRENT, description="This file's state after the run"
    )


class IndexCompletedEvent(BaseModel):
    """Reports one file finishing, successfully or as a skip."""

    event: Literal["completed"] = Field(default="completed", description="The SSE event name")
    data: IndexCompletedEventData = Field(..., description="Result for one file")


class IndexErrorEventData(BaseModel):
    """Payload of the `error` event.

    Reports one file failing. The run continues with the remaining files, so
    a single unreadable upload cannot abort a whole batch.
    """

    # Which file failed, if the failure was file-specific.
    source_key: str = Field(default="", description="Object key that failed")

    # Which stage failed.
    stage: str = Field(..., description="Pipeline stage that failed")

    # Human-readable failure detail.
    message: str = Field(..., description="Description of the failure")


class IndexErrorEvent(BaseModel):
    """Reports a non-fatal failure; the run continues."""

    event: Literal["error"] = Field(default="error", description="The SSE event name")
    data: IndexErrorEventData = Field(..., description="Which file and stage failed, and why")


class IndexSummaryEventData(BaseModel):
    """Payload of the `summary` event, sent once at the very end."""

    # Files embedded in this run.
    indexed: int = Field(default=0, ge=0, description="Files embedded")

    # Files skipped because they were already current.
    skipped: int = Field(default=0, ge=0, description="Files already up to date")

    # Files that failed.
    failed: int = Field(default=0, ge=0, description="Files that failed")

    # Total chunks written across the run.
    total_chunks: int = Field(default=0, ge=0, description="Chunks written across the run")

    # Total leftover chunks pruned across the run.
    total_pruned: int = Field(default=0, ge=0, description="Stale chunks removed")

    # The state of every processed file, so a client can refresh its list
    # without a follow-up request.
    statuses: list[SourceStatus] = Field(
        default_factory=list, description="Final status of each processed file"
    )


class IndexSummaryEvent(BaseModel):
    """Closes the stream with the run's totals."""

    event: Literal["summary"] = Field(default="summary", description="The SSE event name")
    data: IndexSummaryEventData = Field(..., description="Totals for the whole run")


# Union of everything the indexing stream can emit, for the docs module.
IndexStreamEvent = (
    IndexStartedEvent
    | IndexProgressEvent
    | IndexCompletedEvent
    | IndexErrorEvent
    | IndexSummaryEvent
)
