"""Data Phase – Document: represents a raw document before chunking."""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class Document(BaseModel):
    """A raw document ingested into the RAG pipeline.

    This is the starting point of the Data phase – the unprocessed source
    material that will later be split into chunks.
    """

    # Unique identifier for the document (e.g. UUID or filename hash).
    id: str = Field(..., description="Unique document identifier")

    # The full text content of the document.
    content: str = Field(..., min_length=1, description="Raw text content of the document")

    # Human-readable source name (file path, URL, title, etc.).
    source: str = Field(..., description="Origin of the document (path, URL, etc.)")

    # Optional key-value metadata (author, tags, language, …).
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary metadata attached to the document",
    )

    # Timestamp indicating when the document was ingested.
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of document ingestion",
    )

    # Optional total character count, useful for analytics / limits.
    char_count: Optional[int] = Field(
        default=None,
        description="Total character count of the content",
    )
