"""Artifacts — stored by-products of extraction, served back to a reader.

Today that means tables: a document's tables are lifted out at index time and
what gets embedded is a prose description linking back to them.  These are the
shapes the /artifacts endpoints answer with when such a link is followed.
"""

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.extraction import ExtractedTable


class TableListResponse(BaseModel):
    """Every table one document currently has stored."""

    # The document the tables were extracted from.
    document_id: str = Field(..., description="Document id the tables belong to")

    # The tables, in document order.
    tables: list[ExtractedTable] = Field(
        default_factory=list, description="The document's stored tables"
    )


class TableArtifact(BaseModel):
    """One stored table, resolved from a table link."""

    document_id: str = Field(..., description="Document id the table belongs to")

    table_id: str = Field(..., description="The table's id within the document")

    # The table itself, as extracted — a markdown pipe table.
    markdown: str = Field(..., description="The table's content as markdown")

    page: Optional[int] = Field(default=None, ge=1, description="Page holding the table")

    caption: Optional[str] = Field(default=None, description="The table's caption")
