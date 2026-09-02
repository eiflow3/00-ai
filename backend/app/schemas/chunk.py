"""Data Phase – Chunk: a text segment produced by splitting a Document."""

from typing import Optional

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A text chunk created by splitting a Document with overlap.

    During the Data phase, large documents are broken into smaller segments.
    An overlap (repeated tokens at chunk boundaries) ensures context is not
    lost when a sentence or concept spans two consecutive chunks.
    """

    # Unique identifier for this chunk.
    id: str = Field(..., description="Unique chunk identifier")

    # Reference back to the source document.
    document_id: str = Field(..., description="ID of the parent Document")

    # The actual text content of this chunk.
    content: str = Field(..., min_length=1, description="Text content of the chunk")

    # Zero-based position of this chunk within its parent document.
    chunk_index: int = Field(
        ...,
        ge=0,
        description="Position of this chunk in the document (0-based)",
    )

    # Number of characters/tokens that overlap with the previous chunk.
    overlap: int = Field(
        default=0,
        ge=0,
        description="Number of overlapping characters with the preceding chunk",
    )

    # Character offset where this chunk starts in the original document.
    start_offset: Optional[int] = Field(
        default=None,
        ge=0,
        description="Character offset in the source document where this chunk begins",
    )

    # Character offset where this chunk ends in the original document.
    end_offset: Optional[int] = Field(
        default=None,
        ge=0,
        description="Character offset in the source document where this chunk ends",
    )

    # Total number of characters in this chunk.
    char_count: Optional[int] = Field(
        default=None,
        description="Character count of the chunk content",
    )

    # First source-document page this chunk's text touches. None for formats
    # without pages — absence, not zero, so nothing invents a page number.
    page_start: Optional[int] = Field(
        default=None,
        ge=1,
        description="First source page this chunk touches, when pages exist",
    )

    # Last source-document page this chunk's text touches.
    page_end: Optional[int] = Field(
        default=None,
        ge=1,
        description="Last source page this chunk touches, when pages exist",
    )
