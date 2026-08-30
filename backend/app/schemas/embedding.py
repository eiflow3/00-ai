"""Data Phase – Embedding: vector representation of a text chunk."""

from pydantic import BaseModel, Field

from app.services.embeddings import DEFAULT_EMBEDDING_MODEL


class Embedding(BaseModel):
    """A numerical vector that captures the semantic meaning of a chunk.

    After chunking, each chunk is passed through an embedding model to produce
    a dense vector.  These vectors are stored in a vector database so they can
    be efficiently compared during the Retrieval phase.
    """

    # Unique identifier for this embedding record.
    id: str = Field(..., description="Unique embedding identifier")

    # Reference back to the chunk this embedding was created from.
    chunk_id: str = Field(..., description="ID of the source Chunk")

    # The dense vector produced by the embedding model.
    vector: list[float] = Field(..., description="Dense embedding vector")

    # Name/version of the embedding model used (e.g. "text-embedding-3-small").
    model: str = Field(..., description="Embedding model used to create this vector")

    # Dimensionality of the vector (must match the model's output size).
    dimensions: int = Field(..., gt=0, description="Number of dimensions in the vector")


class EmbeddingRequest(BaseModel):
    """Request body for creating embeddings from raw text.

    Accepts a list of texts and the target embedding model; returns Embedding
    objects for each input.
    """

    # List of text strings to embed.
    texts: list[str] = Field(
        ...,
        min_length=1,
        description="List of text strings to embed",
    )

    # The embedding model to use.
    model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL,
        description="Embedding model name (e.g. text-embedding-3-small)",
    )
