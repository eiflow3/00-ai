"""Data Phase – Extraction: what reading a source file's bytes produced.

Plain text formats fill only `text`.  Structured formats — a PDF is the first —
also report where each page lands in that text and which tables were found, so
that pages can ride along with chunks and tables can be stored as artifacts of
their own.
"""

from typing import Optional

from pydantic import BaseModel, Field


class PageSpan(BaseModel):
    """Where one page of the source document lies within the extracted text."""

    # 1-based page number, as a reader of the original file would count it.
    page: int = Field(..., ge=1, description="Page number in the source document")

    # Character offset where this page's text begins.
    start_offset: int = Field(..., ge=0, description="Start offset in the extracted text")

    # Character offset just past this page's text.
    end_offset: int = Field(..., ge=0, description="End offset in the extracted text")


class ExtractedTable(BaseModel):
    """One table lifted out of the document, kept verbatim as its own artifact."""

    # Sequential, deterministic id within the document: "table-001", "table-002" …
    table_id: str = Field(..., description="Table id, unique within the document")

    # The table itself, as a markdown pipe table.
    markdown: str = Field(..., description="The table's content as markdown")

    # Which page the table sits on, when the format knows pages.
    page: Optional[int] = Field(default=None, ge=1, description="Page holding the table")

    # The caption the document gave it, when one was found.
    caption: Optional[str] = Field(default=None, description="The table's caption")


class ExtractionResult(BaseModel):
    """The canonical text of one source file, plus its structure.

    `text` is *the* string everything downstream consumes — it is what gets
    stored as the derived markdown, what preview shows, and what the chunker
    cuts, so offsets in `pages` always describe exactly this string.
    """

    # The document's full text. For structured formats this is markdown with
    # visible page markers; for plain text it is the decoded file.
    text: str = Field(..., description="Canonical extracted text")

    # Page boundaries within `text`. Empty for formats without pages.
    pages: list[PageSpan] = Field(default_factory=list, description="Page spans in `text`")

    # Tables lifted out of the document. Empty for formats without them.
    tables: list[ExtractedTable] = Field(default_factory=list, description="Extracted tables")

    # Etag of the source object this extraction describes. Stamped when the
    # result is persisted, so a later read can tell the file changed since.
    source_etag: Optional[str] = Field(
        default=None, description="Etag of the source bytes this was extracted from"
    )

    # Non-fatal trouble during extraction — a page that failed to parse, say.
    # Degraded coverage the caller should surface, never a failed document.
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal extraction problems"
    )
