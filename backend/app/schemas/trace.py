"""Trace schemas — the durable record of one answered chat request.

A trace is what the chat endpoint *did*: the question, the chunks retrieval
found, the answer generation wrote, and what each stage cost.  It is captured
for every request, whether or not anyone ever judges it, because the judgement
comes later and cannot reconstruct what the index held at the time.

The chunk text is stored in full rather than referenced by id.  Chunk ids are
positional (`{document_id}#{nnnnn}`), so re-indexing at a different chunk size
silently repoints an old id at different text — which would quietly corrupt the
one question a trace exists to answer.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TraceState(str, Enum):
    """How a chat request ended."""

    # The answer streamed to completion.
    COMPLETED = "completed"

    # Generation failed, so there is no usable answer.
    FAILED = "failed"

    # The client disconnected or aborted before the answer finished.
    CANCELLED = "cancelled"


class TraceChunk(BaseModel):
    """One chunk retrieval returned, captured as it was at answer time.

    `dropped` marks a chunk the score threshold excluded from the prompt. Those
    are kept because a near-miss is evidence: an answer that failed while the
    right chunk sat just under the threshold is a tuning problem, not a
    retrieval or generation fault.
    """

    # Position in the ranked result, zero-based and best-first.
    rank: int = Field(..., ge=0, description="Position in the ranked results, best first")

    # The vector id this chunk was stored under at answer time.
    chunk_id: str = Field(..., description="Vector id of the matched chunk")

    # The parent document's derived id.
    document_id: str = Field(default="", description="Id of the parent document")

    # The object key the chunk's text came from.
    source_key: str = Field(default="", description="Object key of the source file")

    # Similarity between the query vector and this chunk's vector.
    score: float = Field(..., ge=0.0, le=1.0, description="Similarity score, 0–1")

    # The chunk's text, stored verbatim — this is the evidence.
    content: str = Field(default="", description="Full chunk text as it was retrieved")

    # Fingerprint of that text, so a later re-index can be told it changed.
    content_hash: str = Field(default="", description="Fingerprint of the chunk text")

    # Length of the stored text, so a listing can show size without the body.
    char_count: int = Field(default=0, ge=0, description="Characters of chunk text")

    # True when the score threshold kept this chunk out of the prompt.
    dropped: bool = Field(
        default=False, description="Excluded from the prompt by the score threshold"
    )


class Trace(BaseModel):
    """One answered chat request, without its chunks or evaluations."""

    # Stable id, minted when the stream opens and sent to the client at once.
    trace_id: str = Field(..., description="Id of this chat request")

    created_at: datetime = Field(..., description="When the request was made")

    # --- What was asked and answered ---------------------------------------

    question: str = Field(..., description="The user's question")
    answer: str = Field(default="", description="The answer as it was streamed")

    # --- Generation settings ------------------------------------------------

    provider: str = Field(default="", description="LLM provider that answered")
    model: str = Field(default="", description="Model that answered")
    temperature: float = Field(default=1.0, description="Sampling temperature used")
    system_prompt: str = Field(default="", description="System prompt in force, if any")

    # --- Retrieval settings and outcome -------------------------------------

    use_rag: bool = Field(default=True, description="Whether retrieval ran at all")
    top_k: int = Field(default=0, ge=0, description="Chunks requested from the store")
    score_threshold: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Minimum score a chunk had to reach"
    )
    embedding_model: str = Field(default="", description="Model used to embed the query")
    total_searched: int = Field(
        default=0, ge=0, description="Candidates the vector store returned"
    )
    chunk_count: int = Field(
        default=0, ge=0, description="Chunks that reached the prompt"
    )
    top_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Best similarity score retrieved"
    )

    # --- Outcome ------------------------------------------------------------

    state: TraceState = Field(default=TraceState.COMPLETED, description="How it ended")
    error_stage: str = Field(default="", description="Stage that failed, if any")
    error_message: str = Field(default="", description="Why that stage failed")

    # --- Cost and timing ----------------------------------------------------

    retrieval_ms: int = Field(default=0, ge=0, description="Milliseconds spent retrieving")
    generation_ms: int = Field(default=0, ge=0, description="Milliseconds spent generating")
    total_ms: int = Field(default=0, ge=0, description="Milliseconds end to end")
    input_tokens: int = Field(default=0, ge=0, description="Prompt tokens billed")
    output_tokens: int = Field(default=0, ge=0, description="Completion tokens billed")
    total_cost: float = Field(default=0.0, ge=0.0, description="Dollar cost of generation")

    # --- Judgement rollup ---------------------------------------------------
    # Denormalised so the list screen can show a verdict per row without
    # loading every evaluation attached to every trace.

    evaluation_count: int = Field(
        default=0, ge=0, description="Live (non-deleted) evaluations on this trace"
    )
    verdicts: dict[str, str] = Field(
        default_factory=dict,
        description="Latest live verdict per target, e.g. {'retrieval': 'bad'}",
    )


class TraceDetail(BaseModel):
    """One trace with everything attached to it."""

    trace: Trace = Field(..., description="The request itself")
    chunks: list[TraceChunk] = Field(
        default_factory=list, description="Every chunk retrieval returned, best first"
    )
    # Imported lazily by the router's response model to avoid a schema cycle;
    # typed loosely here so this module does not depend on evaluation.
    evaluations: list["Evaluation"] = Field(
        default_factory=list, description="Every judgement made on this trace"
    )


class TracePage(BaseModel):
    """One page of traces, newest first."""

    traces: list[Trace] = Field(default_factory=list, description="The page's rows")
    total: int = Field(default=0, ge=0, description="Rows matching the filter in all")
    limit: int = Field(default=0, ge=0, description="Page size that was applied")
    offset: int = Field(default=0, ge=0, description="Rows skipped before this page")


class TraceDeleteResponse(BaseModel):
    """Result of discarding a trace."""

    trace_id: str = Field(..., description="The trace that was discarded")
    deleted: bool = Field(..., description="False when no such trace existed")


# Resolved after import so TraceDetail can name Evaluation without a cycle.
from app.schemas.evaluation import Evaluation  # noqa: E402

TraceDetail.model_rebuild()

__all__ = [
    "Trace",
    "TraceChunk",
    "TraceDeleteResponse",
    "TraceDetail",
    "TracePage",
    "TraceState",
]
