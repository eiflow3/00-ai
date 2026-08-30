"""Generation Phase – build the prompt, call the LLM, return the response."""

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.retrieval import RetrievedChunk


class GenerationRequest(BaseModel):
    """Request body for the Generation phase.

    Combines the user's original query with the retrieved context chunks into
    a structured payload that can be turned into an LLM prompt.
    """

    # The user's original question.
    query: str = Field(..., min_length=1, description="The user's original question")

    # Context chunks retrieved from the vector store.
    context_chunks: list[RetrievedChunk] = Field(
        default_factory=list,
        description="Retrieved chunks to include as context for the LLM",
    )

    # Optional system-level prompt to steer the LLM's behaviour.
    system_prompt: Optional[str] = Field(
        default=None,
        description="Optional system prompt to guide LLM behaviour",
    )

    # LLM model to use for generation.
    model: str = Field(
        default="gpt-4o-mini",
        description="LLM model name (e.g. gpt-4o-mini, gpt-4o)",
    )

    # Sampling temperature for the LLM (0 = deterministic, higher = creative).
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for the LLM",
    )


class GenerationResponse(BaseModel):
    """Response from the Generation phase.

    Contains the LLM's synthesised answer along with metadata about which
    sources were used and token consumption.
    """

    # The LLM's generated answer.
    answer: str = Field(..., description="Generated answer from the LLM")

    # Echo the original query for traceability.
    query: str = Field(..., description="The original user query")

    # IDs of chunks that were included as context.
    source_chunk_ids: list[str] = Field(
        default_factory=list,
        description="IDs of chunks used as context",
    )

    # Name of the LLM model that produced the answer.
    model: str = Field(..., description="LLM model that generated the answer")

    # Token usage breakdown (prompt + completion).
    prompt_tokens: Optional[int] = Field(
        default=None,
        description="Number of tokens in the prompt",
    )
    completion_tokens: Optional[int] = Field(
        default=None,
        description="Number of tokens in the generated response",
    )
    total_tokens: Optional[int] = Field(
        default=None,
        description="Total tokens used (prompt + completion)",
    )
