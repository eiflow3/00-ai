"""Data embedding pipeline — request body, run records and streamed events.

Indexing runs long enough that it cannot be the response to the request that
asked for it: a client that reloads would cancel the work. So a request only
*enqueues*, and progress is read from a separate stream that any client can
open, close and reopen.

Three kinds of payload live here: what a caller sends (`IndexRequest`), what
enqueuing and the runs listing return (`EnqueueResponse`, `IndexRun`), and
every event the stream can emit — documented in app.docs.sources.
"""

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.chunking import DEFAULT_STRATEGY, ChunkStrategy, ChunkingConfig
from app.schemas.governance import GovernanceFindingSummary, GovernanceMode
from app.schemas.source import IndexState, SourceStatus
from app.services.chunking.tokens import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE
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

    # How the text is cut.
    strategy: ChunkStrategy = Field(
        default=DEFAULT_STRATEGY,
        description="How to cut the document's text into chunks",
    )

    # Which chunking variant to write to. Empty writes to the index the app
    # answers from; naming one writes into that experiment's own space
    # instead, where it cannot affect a production answer.
    variant: str = Field(
        default="",
        description=(
            "Chunking variant to embed into, e.g. 'recursive-512-64'. Empty "
            "means the production index. A variant's name fully determines "
            "how it is cut, so `strategy`, `chunk_size` and `chunk_overlap` "
            "are ignored when one is given."
        ),
    )

    # Model used to embed the chunks. Must match what queries will use.
    embedding_model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL,
        description="Embedding model used to embed the chunks",
    )

    # Governance mode override for this run. None runs under the server's
    # configured default; 'off' indexes content exactly as extracted, with the
    # run stamped unscreened so that choice is visible later.
    governance_mode: Optional[GovernanceMode] = Field(
        default=None,
        description=(
            "Override the governance mode for this run: 'off', 'audit_only' "
            "or 'enforce'. Omit to use the server default."
        ),
    )

    @property
    def chunking(self) -> ChunkingConfig:
        """The configuration this request asks for, ignoring any variant.

        A variant overrides this outright; see `services.chunk_variants.resolve`,
        which is the one place that precedence is decided.
        """
        return ChunkingConfig(
            strategy=self.strategy,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )


class IndexStartedEventData(BaseModel):
    """Payload of the `started` event, sent once before any file is processed."""

    # The run this stream belongs to. A client keeps it so a reload can ask for
    # the same run again rather than starting a new one.
    job_id: str = Field(default="", description="Id of the run this stream reports on")

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


class IndexQueuedEventData(BaseModel):
    """Payload of the `queued` event, sent when the queue grows mid-run.

    One worker drains one queue, so pressing Index again during a run adds to
    the work in flight rather than starting a rival run. The client's totals
    have to follow, which is what this event is for.
    """

    # Files added by the request that triggered this event.
    added: list[str] = Field(
        default_factory=list, description="Keys just added to the queue"
    )

    # Everything now waiting, in queue order.
    pending: list[str] = Field(
        default_factory=list, description="Keys still waiting, in order"
    )

    # Files this run has taken on in total, finished ones included. This is the
    # denominator a progress bar should use.
    total: int = Field(default=0, ge=0, description="Files this run has taken on")


class IndexQueuedEvent(BaseModel):
    """Reports the queue growing while a run is already in flight."""

    event: Literal["queued"] = Field(default="queued", description="The SSE event name")
    data: IndexQueuedEventData = Field(..., description="What was added, and what waits")


class IndexProgressEventData(BaseModel):
    """Payload of the `progress` event, sent as each stage of a file completes."""

    # Which file this concerns.
    source_key: str = Field(..., description="Object key being processed")

    # Which stage just finished: loading, extracting, describing_tables,
    # screening, chunking, embedding or upserting.
    stage: Literal[
        "loading",
        "extracting",
        "describing_tables",
        "screening",
        "chunking",
        "embedding",
        "upserting",
    ] = Field(
        ..., description="Pipeline stage that just completed for this file"
    )

    # Position of this file in the run, 1-based.
    file_number: int = Field(default=0, ge=0, description="1-based position in the run")

    # Files this run has taken on so far. Can grow while the run is in
    # flight, because a later Index click joins this run rather than starting
    # another — so a client should re-read it rather than caching the first one.
    total_files: int = Field(
        default=0, ge=0, description="Files this run has taken on so far"
    )

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

    # How many chunks the file has in the index now.
    chunk_count: int = Field(default=0, ge=0, description="Chunks the file now has")

    # Chunks that did not need embedding because the index already held them,
    # identical and from the same model. Non-zero means a previous interrupted
    # run was resumed rather than repeated.
    reused: int = Field(default=0, ge=0, description="Chunks reused without re-embedding")

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


class IndexGovernanceEventData(BaseModel):
    """Payload of the `governance` event, sent once per screened file.

    What the governance stage found and did to one file, as counts per
    entity type and class — never the matched values themselves. A blocked
    verdict means the file was refused: nothing of it was chunked or
    embedded, and an `error` event follows saying so.
    """

    # Which file was screened.
    source_key: str = Field(..., description="Object key that was screened")

    # The mode the screening ran under, after request/default resolution.
    mode: GovernanceMode = Field(..., description="Mode the screening ran under")

    # False when mode was 'off' — the file was indexed unscreened.
    screened: bool = Field(..., description="Whether screening actually ran")

    # 'blocked' when policy refused the file outright.
    verdict: Literal["allowed", "blocked"] = Field(
        default="allowed", description="Whether the file was allowed through"
    )

    # What was found, grouped: entity type, classification, action, count.
    findings: list[GovernanceFindingSummary] = Field(
        default_factory=list, description="Counts of what was found and done"
    )


class IndexGovernanceEvent(BaseModel):
    """Reports what governance found in one file; the run continues."""

    event: Literal["governance"] = Field(
        default="governance", description="The SSE event name"
    )
    data: IndexGovernanceEventData = Field(
        ..., description="What was found in one file, and what was done"
    )


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

    # Total chunks across every file the run touched.
    total_chunks: int = Field(default=0, ge=0, description="Chunks across the run")

    # Chunks the run did not have to embed, because the index already held
    # them. The measure of what resuming saved.
    total_reused: int = Field(
        default=0, ge=0, description="Chunks reused without re-embedding"
    )

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


class IndexRunState(str, Enum):
    """Where a run stands. Every state but `running` is terminal."""

    # The worker is draining the queue.
    RUNNING = "running"

    # The queue emptied and the worker stopped of its own accord.
    COMPLETED = "completed"

    # The worker stopped on an error that was not scoped to a single file.
    FAILED = "failed"

    # Someone pressed Stop.
    CANCELLED = "cancelled"

    # The server stopped while this run was in flight. Recorded on the next
    # startup, because a run marked `running` with no process behind it is a
    # lie — and one a person reading the history would be misled by.
    ABANDONED = "abandoned"


class IndexRun(BaseModel):
    """One indexing run, live or finished.

    What `GET /sources/index/runs` returns. A client asks for this on load: if
    a run is in flight it holds the id needed to attach to its stream, which is
    how progress survives a reload.
    """

    # Id to attach to, and the key into run history.
    job_id: str = Field(..., description="Id of this run")

    state: IndexRunState = Field(..., description="Where this run stands")

    # Files still waiting, in order.
    pending: list[str] = Field(
        default_factory=list, description="Keys still waiting, in order"
    )

    # The file being embedded right now, if any.
    current: str = Field(default="", description="Key being embedded right now")

    # Files this run has taken on in total, finished ones included.
    total: int = Field(default=0, ge=0, description="Files this run has taken on")

    # Outcome counts, filled in as the run proceeds.
    indexed: int = Field(default=0, ge=0, description="Files embedded")
    skipped: int = Field(default=0, ge=0, description="Files skipped")
    failed: int = Field(default=0, ge=0, description="Files that failed")

    # Chunks the run did not have to embed.
    total_reused: int = Field(
        default=0, ge=0, description="Chunks reused without re-embedding"
    )

    started_at: Optional[datetime] = Field(default=None, description="When it began")
    finished_at: Optional[datetime] = Field(default=None, description="When it ended")

    # Why it failed, when it did.
    error: str = Field(default="", description="Failure detail, if it failed")

    # Position of the last event emitted. A client re-attaching passes this as
    # `after` so it resumes rather than replaying what it already has.
    last_cursor: int = Field(
        default=-1, description="Cursor of the last event emitted by this run"
    )


class EnqueueResponse(BaseModel):
    """Result of asking for files to be indexed.

    The request only enqueues — it does not wait, and it does not stream. The
    job id it returns is what a client opens the event stream with, which means
    starting a run and returning to one after a reload take the same path.
    """

    # The run the accepted files joined, whether newly started or already going.
    job_id: str = Field(..., description="Run the accepted files joined")

    # Files added to the queue by this request.
    accepted: list[str] = Field(
        default_factory=list, description="Keys added to the queue"
    )

    # Files that were already queued or being embedded. Not an error — the work
    # is already going to happen, so a second click is simply redundant.
    already_queued: list[str] = Field(
        default_factory=list, description="Keys already queued or in flight"
    )

    # Files refused because the queue is full. Named rather than silently
    # dropped, so a caller knows exactly what did not get in.
    rejected: list[str] = Field(
        default_factory=list, description="Keys refused because the queue is full"
    )

    # Keys the caller named that are not in storage at all.
    missing: list[str] = Field(
        default_factory=list, description="Keys with no object behind them"
    )

    # The configured ceiling, so a client can explain a rejection without
    # having the limit hardcoded on its side.
    limit: int = Field(default=0, ge=0, description="Most files that may wait at once")

    # Everything now waiting, in queue order.
    pending: list[str] = Field(
        default_factory=list, description="Keys still waiting, in order"
    )


# Union of everything the indexing stream can emit, for the docs module.
IndexStreamEvent = (
    IndexStartedEvent
    | IndexQueuedEvent
    | IndexProgressEvent
    | IndexGovernanceEvent
    | IndexCompletedEvent
    | IndexErrorEvent
    | IndexSummaryEvent
)
