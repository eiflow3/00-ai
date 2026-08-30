"""Retrieval Phase – query the vector store and get relevant chunks."""

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    """User query that kicks off the Retrieval phase.

    The raw text query is embedded using the same model from the Data phase,
    then compared against stored vectors via similarity search.
    """

    # The user's natural-language question or prompt.
    query: str = Field(..., min_length=1, description="User's search query text")

    # Maximum number of chunks to retrieve.
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Number of most-relevant chunks to return",
    )

    # Minimum similarity score threshold (0-1) to filter weak matches.
    score_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum similarity score to include a result",
    )


class RetrievedChunk(BaseModel):
    """A single chunk returned by the similarity search.

    Wraps the chunk content together with its similarity score so the caller
    can see how relevant each result is.
    """

    # ID of the matched chunk.
    chunk_id: str = Field(..., description="ID of the matched Chunk")

    # ID of the document the chunk belongs to.
    document_id: str = Field(..., description="ID of the parent Document")

    # The text content of the matched chunk.
    content: str = Field(..., description="Text content of the matched chunk")

    # Cosine-similarity (or equivalent) score between query and chunk vectors.
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Similarity score between query and chunk (0–1)",
    )

    # Source info for traceability.
    source: str = Field(default="", description="Origin of the parent document")


class RetrievalResult(BaseModel):
    """Complete result set from a retrieval query.

    Groups the original query together with all matched chunks for easy
    downstream consumption in the Generation phase.
    """

    # Echo the original query for traceability.
    query: str = Field(..., description="The original query text")

    # Ordered list of retrieved chunks, best match first.
    chunks: list[RetrievedChunk] = Field(
        default_factory=list,
        description="Retrieved chunks ordered by descending similarity score",
    )

    # Total number of chunks that were searched.
    total_searched: int = Field(
        default=0,
        ge=0,
        description="Total number of chunks compared during search",
    )
