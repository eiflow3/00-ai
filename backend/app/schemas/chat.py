"""Chat schema — request body for the POST /chat streaming endpoint."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.retrieval import RetrievedChunk


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
        description="Model name override (e.g. 'gpt-4o-mini', 'claude-sonnet-4-20250514')",
    )

    # Optional system prompt to steer the LLM's behaviour.
    system_prompt: Optional[str] = Field(
        default=None,
        description="Optional system prompt to guide LLM behaviour",
    )

    # Sampling temperature for the LLM.
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (0 = deterministic, higher = creative)",
    )

    # Optional context chunks from the retrieval phase.
    context_chunks: list[RetrievedChunk] = Field(
        default_factory=list,
        description="Retrieved context chunks to include in the prompt",
    )
