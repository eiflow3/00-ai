"""Chat schema — request body for the POST /chat streaming endpoint."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.retrieval import RetrievedChunk
from app.services.cost_tracker import CostBreakdown
from app.services.embeddings import DEFAULT_EMBEDDING_MODEL


class ChatRequest(BaseModel):
    """Request body for the chat endpoint.

    The client specifies which LLM provider and model to use.
    The adapter pattern handles the rest behind the scenes.
    """

    # The user's question or message.
    query: str = Field(..., min_length=1, description="The user's message or question")

    # Which LLM provider to use — determines which adapter handles the request.
    provider: Literal["openai", "claude"] = Field(
        default="openai",
        description="LLM provider to use ('openai' or 'claude')",
    )

    # The specific model name to use (defaults differ per provider).
    model: Optional[str] = Field(
        default=None,
        description="Model name override (e.g. 'gpt-5.6-terra', 'claude-sonnet-5')",
    )

    # Optional system prompt to steer the LLM's behaviour.
    system_prompt: Optional[str] = Field(
        default=None,
        description="Optional system prompt to guide LLM behaviour",
    )

    # Sampling temperature for the LLM.
    temperature: float = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        description=(
            "Sampling temperature. The default is 1.0 because the current "
            "models reject anything else: OpenAI's refuse a non-default value, "
            "and Anthropic's no longer accept the parameter at all."
        ),
    )

    # Optional context chunks from the retrieval phase.
    # When supplied, the endpoint skips its own retrieval and uses these as-is.
    context_chunks: list[RetrievedChunk] = Field(
        default_factory=list,
        description=(
            "Pre-retrieved context chunks. If non-empty, the endpoint skips "
            "its own retrieval step and uses these instead."
        ),
    )

    # --- Retrieval (RAG) controls -------------------------------------------

    # Whether the endpoint should run retrieval before generation.
    use_rag: bool = Field(
        default=True,
        description="Run vector retrieval before generation (ignored if context_chunks is set)",
    )

    # How many chunks to pull from the vector store.
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Number of chunks to retrieve from the vector store",
    )

    # Minimum similarity score required for a chunk to be used.
    score_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Drop retrieved chunks scoring below this value",
    )

    # Embedding model used for the query — must match the stored vectors'.
    embedding_model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL,
        description="Embedding model used to embed the query for retrieval",
    )

    # Which chunking variant to answer from. This is the whole point of the
    # comparison: hold the question, the model, the prompt and top_k still,
    # change only where the chunks came from, and the difference in the answer
    # is the difference the chunking made.
    chunk_variant: str = Field(
        default="",
        description=(
            "Chunking variant to retrieve from, e.g. 'recursive-512-64'. Empty "
            "means the production index."
        ),
    )


class ModelOption(BaseModel):
    """One provider/model pair this deployment offers.

    Returned so a client renders a selector from what is actually configured
    here, rather than from a hardcoded list that drifts out of date.
    """

    # Provider key to send back as `provider` on a chat request.
    provider: str = Field(..., description="Provider key for the chat request")

    # Display name for the provider.
    provider_label: str = Field(..., description="Human-readable provider name")

    # Model id to send back as `model` on a chat request.
    model: str = Field(..., description="Model id for the chat request")

    # Display name for the model.
    model_label: str = Field(..., description="Human-readable model name")

    # Whether this deployment has the credentials to use it.
    available: bool = Field(
        default=True, description="Whether this option can be used here"
    )

    # What is missing or uncertain, written for a person to act on.
    detail: str = Field(default="", description="Any caveat about using this option")

    # Whether the cost tracker knows this model's prices. An unpriced model
    # still answers, but reports zero cost.
    priced: bool = Field(
        default=True, description="False when cost for this model reports as zero"
    )


class ChatStreamMessageEvent(BaseModel):
    """Represents a standard text chunk event in the SSE stream.
    
    In SSE, this is typically sent as an unnamed event (implicitly 'message') 
    with the text delta in the data field.
    """
    data: str = Field(..., description="A chunk of text generated by the LLM")


class TraceEventData(BaseModel):
    """Payload of the `trace` SSE event.

    Sent as the very first event so a client holds the id before anything can
    fail. Every later judgement on this exchange is filed against it.
    """

    # Id of the durable record this request is being written to.
    trace_id: str = Field(..., description="Id of the trace recording this request")


class ChatStreamTraceEvent(BaseModel):
    """Represents the trace event in the SSE stream.

    Sent first, with the event name 'trace'.
    """

    event: Literal["trace"] = Field(default="trace", description="The SSE event name")
    data: TraceEventData = Field(..., description="The id this request is recorded under")


class RetrievalEventData(BaseModel):
    """Payload of the `retrieval` SSE event.

    Sent once, before any text deltas, so the client can render citations
    and similarity scores while the answer is still streaming.
    """

    # Echo of the query that was embedded and searched.
    query: str = Field(..., description="The query used for the vector search")

    # Matched chunks, best score first, each carrying its similarity score.
    chunks: list[RetrievedChunk] = Field(
        default_factory=list,
        description="Retrieved chunks with similarity scores, best match first",
    )

    # How many candidates the vector store returned before filtering.
    total_searched: int = Field(
        default=0, ge=0, description="Number of candidate matches returned by the store"
    )

    # Embedding model used for the query.
    embedding_model: str = Field(default="", description="Model used to embed the query")


class ChatStreamRetrievalEvent(BaseModel):
    """Represents the retrieval event in the SSE stream.

    Sent at the start of the stream with the event name 'retrieval'.
    """

    event: Literal["retrieval"] = Field(default="retrieval", description="The SSE event name")
    data: RetrievalEventData = Field(..., description="Retrieved chunks and their scores")


class StageEventData(BaseModel):
    """Payload of the `stage` SSE event.

    One step of the pipeline, reported as it starts and again as it ends. A
    client renders these as a timeline of what the request spent its time on;
    it must not enumerate the stages it knows about, because a stage added to
    the pipeline later arrives here carrying its own wording.
    """

    # Position in the timeline. The start and end of one stage share it, so a
    # client updates a row rather than appending a second one.
    sequence: int = Field(..., ge=1, description="Position of this stage in the timeline")

    # Stable id for the stage, safe to branch on (e.g. "embedding").
    name: str = Field(..., description="Machine-readable stage id")

    # Wording written for a person, supplied by the stage itself.
    label: str = Field(..., description="Human-readable stage name, ready to display")

    # Whether the stage is running, finished, or failed.
    status: Literal["started", "completed", "failed"] = Field(
        ..., description="Whether the stage is running, finished, or failed"
    )

    # How long the stage took. Zero on the `started` event.
    elapsed_ms: int = Field(
        default=0, ge=0, description="Duration in milliseconds, 0 while the stage runs"
    )

    # What the stage produced, or why it failed — one line, for display.
    detail: str = Field(
        default="", description="What the stage produced, or the failure message"
    )


class ChatStreamStageEvent(BaseModel):
    """Represents one pipeline stage's start or end in the SSE stream.

    Sent with the event name 'stage', interleaved with the rest of the stream.
    """

    event: Literal["stage"] = Field(default="stage", description="The SSE event name")
    data: StageEventData = Field(..., description="The stage, and how long it took")


class ErrorEventData(BaseModel):
    """Payload of the `error` SSE event.

    Emitted for a non-fatal failure in one pipeline stage; the stream
    continues afterwards in a degraded form.
    """

    # Which pipeline stage failed (e.g. "retrieval").
    stage: str = Field(..., description="Pipeline stage that failed")

    # Human-readable failure detail.
    message: str = Field(..., description="Description of the failure")


class ChatStreamErrorEvent(BaseModel):
    """Represents a non-fatal error event in the SSE stream.

    Sent with the event name 'error'; the stream is not terminated.
    """

    event: Literal["error"] = Field(default="error", description="The SSE event name")
    data: ErrorEventData = Field(..., description="Which stage failed, and why")


class ChatStreamUsageEvent(BaseModel):
    """Represents the final usage and cost event in the SSE stream.
    
    Sent at the very end of the stream with the event name 'usage'.
    """
    event: Literal["usage"] = Field(default="usage", description="The SSE event name")
    data: CostBreakdown = Field(..., description="Token usage and calculated cost breakdown")

