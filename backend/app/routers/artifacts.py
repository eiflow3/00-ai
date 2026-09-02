"""/artifacts endpoints — reading back what extraction stored.

A table link inside an embedded chunk carries a document id and a table id;
these two endpoints are what the client resolves them against.  HTTP only:
lookups happen in app.services.derived_artifacts.
"""

from fastapi import APIRouter, HTTPException

from app.docs.artifacts import (
    ARTIFACTS_TAG,
    GET_TABLE_DESCRIPTION,
    GET_TABLE_RESPONSES,
    LIST_TABLES_DESCRIPTION,
    LIST_TABLES_RESPONSES,
)
from app.schemas.artifact import TableArtifact, TableListResponse
from app.services import derived_artifacts

router = APIRouter(prefix="/artifacts", tags=[ARTIFACTS_TAG])


@router.get(
    "/{document_id}/tables",
    response_model=TableListResponse,
    summary="List a document's stored tables",
    response_description="The document's tables, in document order.",
    description=LIST_TABLES_DESCRIPTION,
    responses=LIST_TABLES_RESPONSES,
)
async def list_tables(document_id: str) -> TableListResponse:
    """List every table stored for one document.

    Args:
        document_id: The document id a table link carries.

    Returns:
        The tables, empty when the document has none.
    """
    return TableListResponse(
        document_id=document_id,
        tables=await derived_artifacts.list_tables(document_id),
    )


@router.get(
    "/{document_id}/tables/{table_id}",
    response_model=TableArtifact,
    summary="Read one stored table",
    response_description="The table verbatim, with its page and caption.",
    description=GET_TABLE_DESCRIPTION,
    responses=GET_TABLE_RESPONSES,
)
async def get_table(document_id: str, table_id: str) -> TableArtifact:
    """Resolve one table link to the stored table.

    Args:
        document_id: The document id from the link.
        table_id: The table id from the link.

    Returns:
        The stored table.

    Raises:
        HTTPException: 404 when the document holds no such table.
    """
    table = await derived_artifacts.get_table(document_id, table_id)
    if table is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document {document_id!r} has no stored table {table_id!r}.",
        )

    return TableArtifact(
        document_id=document_id,
        table_id=table.table_id,
        markdown=table.markdown,
        page=table.page,
        caption=table.caption,
    )
