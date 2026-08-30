"""/sources endpoints — the data embedding pipeline's HTTP surface.

The counterpart to the chat router: where that one answers questions from the
index, these manage what goes into it.  The router stays thin — it validates
input, delegates each operation to a service, and serialises the result.  The
storage-versus-index comparison, the chunking, and the embedding all live in
app.services.
"""

import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.docs.sources import (
    DEINDEX_DESCRIPTION,
    GET_SOURCE_DESCRIPTION,
    GET_SOURCE_RESPONSES,
    INDEX_SOURCES_DESCRIPTION,
    INDEX_SOURCES_RESPONSES,
    LIST_SOURCES_DESCRIPTION,
    SOURCES_TAG,
)
from app.schemas.ingestion import IndexRequest
from app.schemas.source import (
    DeindexResponse,
    IndexState,
    SourceDetail,
    SourceStatus,
)
from app.services import index_catalog, ingestion, sync_status

router = APIRouter(prefix="/sources", tags=[SOURCES_TAG])


def _sse(event: BaseModel) -> dict:
    """Serialise a typed stream event into the dict sse-starlette expects.

    Going through the schema models keeps every event's payload defined in one
    place — the same models the OpenAPI docs describe.
    """
    return {"event": event.event, "data": event.data.model_dump_json()}


@router.get(
    "",
    response_model=list[SourceStatus],
    summary="List source files and the state of their embeddings",
    response_description="Every source file, joined with what the index holds for it.",
    description=LIST_SOURCES_DESCRIPTION,
)
async def list_sources(
    prefix: str = Query(default="", description="Restrict to keys with this prefix"),
    state: Optional[IndexState] = Query(
        default=None, description="Return only files in this state"
    ),
) -> list[SourceStatus]:
    """List every source file with its index state.

    Args:
        prefix: Restrict the listing to keys beginning with this prefix.
        state: Return only files in this state.

    Returns:
        One status row per file, newest change first, orphans last.
    """
    statuses = await sync_status.list_statuses(prefix)

    if state is not None:
        statuses = [status for status in statuses if status.state == state]

    return statuses


@router.post(
    "/index",
    summary="Run the data embedding pipeline",
    response_description="SSE stream of the run's progress, ending with a summary.",
    description=INDEX_SOURCES_DESCRIPTION,
    responses=INDEX_SOURCES_RESPONSES,
)
async def index_sources(body: IndexRequest):
    """Embed source files into the vector index, streaming progress.

    1. Decides which files to process — the named keys, or whatever is stale.
    2. For each: loads it, splits it into chunks, embeds them, upserts them.
    3. Prunes chunks a shrinking file no longer produces.
    4. Closes with a summary carrying each file's re-read status.
    """
    # Reject an impossible chunk geometry before the stream opens: once SSE has
    # started, the status code is already sent and cannot report a bad request.
    if body.chunk_overlap >= body.chunk_size:
        raise HTTPException(
            status_code=400,
            detail=(
                f"chunk_overlap ({body.chunk_overlap}) must be smaller than "
                f"chunk_size ({body.chunk_size}); otherwise chunking cannot advance."
            ),
        )

    async def event_generator() -> AsyncIterator[dict]:
        """Serialise each event the pipeline yields."""
        async for event in ingestion.run(body):
            yield _sse(event)

    return EventSourceResponse(event_generator())


@router.get(
    "/{source_key:path}",
    response_model=SourceDetail,
    summary="Get one source file and its indexed chunks",
    response_description="The file's state, plus every chunk indexed from it.",
    description=GET_SOURCE_DESCRIPTION,
    responses=GET_SOURCE_RESPONSES,
)
async def get_source(source_key: str) -> SourceDetail:
    """Return one file's state together with its indexed chunks.

    Args:
        source_key: The object key within the bucket. Declared as a path
            parameter so keys containing slashes are matched whole.

    Returns:
        The file's status and its chunks, in document order.

    Raises:
        HTTPException: 404 when neither storage nor the index knows the key.
    """
    detail = await sync_status.get_detail(source_key)

    # Neither side has ever heard of this key — not merely un-indexed.
    if detail.status.source is None and detail.status.indexed is None:
        raise HTTPException(status_code=404, detail=f"No source at key: {source_key}")

    return detail


@router.delete(
    "/{source_key:path}/index",
    response_model=DeindexResponse,
    summary="Delete a file's vectors from the index",
    response_description="How many vectors were removed.",
    description=DEINDEX_DESCRIPTION,
)
async def deindex_source(source_key: str) -> DeindexResponse:
    """Remove a file's vectors, leaving the file itself in object storage.

    Args:
        source_key: The object key whose vectors should be removed.

    Returns:
        The key, and how many vectors were deleted. Zero means nothing was
        indexed under that key — which is not an error, it is the end state
        the caller asked for.
    """
    deleted = await index_catalog.delete_document(source_key)
    return DeindexResponse(source_key=source_key, deleted=deleted)
