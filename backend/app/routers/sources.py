"""/sources endpoints — the data embedding pipeline's HTTP surface.

The counterpart to the chat router: where that one answers questions from the
index, these manage what goes into it.  The router stays thin — it validates
input, delegates each operation to a service, and serialises the result.  The
storage-versus-index comparison, the chunking, and the embedding all live in
app.services.
"""

import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.docs.sources import (
    DEINDEX_DESCRIPTION,
    GET_SOURCE_DESCRIPTION,
    GET_SOURCE_RESPONSES,
    INDEX_SOURCES_DESCRIPTION,
    INDEX_SOURCES_RESPONSES,
    LIST_SOURCES_DESCRIPTION,
    REPLACE_DESCRIPTION,
    REPLACE_RESPONSES,
    SOURCES_TAG,
    UPLOAD_DESCRIPTION,
    UPLOAD_RESPONSES,
)
from app.schemas.ingestion import IndexRequest
from app.schemas.source import (
    DeindexResponse,
    IndexState,
    SourceDetail,
    SourceStatus,
    UploadResponse,
)
from app.services import index_catalog, ingestion, sync_status, uploads
from app.services.uploads import UploadRejected

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


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Upload a new source file",
    response_description="The stored file's state, ready to be indexed.",
    description=UPLOAD_DESCRIPTION,
    responses=UPLOAD_RESPONSES,
)
async def upload_source(
    response: Response,
    file: UploadFile = File(..., description="The file to store"),
    prefix: str = Form(default="", description="Folder to place the file under"),
) -> UploadResponse:
    """Store a new file in object storage, leaving it unindexed.

    Args:
        response: Used to distinguish a created file from one already stored.
        file: The uploaded file.
        prefix: Folder to place it under, if any.

    Returns:
        The file's state after the write.

    Raises:
        HTTPException: 400 if the file is unacceptable, 409 if the key holds
            different content.
    """
    data = await file.read()

    try:
        stored, created = await uploads.upload_new(file.filename or "", data, prefix)
    except UploadRejected as exc:
        # A name taken by *different* content is a conflict, not a malformed
        # request — the client's recourse is to replace, a different endpoint.
        status_code = 409 if "already exists" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))

    # 201 for a file this call created, 200 for one that was already there —
    # so a retry of a request whose response was lost still reads as success.
    response.status_code = 201 if created else 200

    return UploadResponse(
        status=await sync_status.get_status(stored.key), created=created
    )


@router.put(
    "/{source_key:path}",
    response_model=UploadResponse,
    summary="Replace a file's contents and discard its embeddings",
    response_description="The file's state after the write, and how many vectors were pruned.",
    description=REPLACE_DESCRIPTION,
    responses=REPLACE_RESPONSES,
)
async def replace_source(
    source_key: str,
    file: UploadFile = File(..., description="The replacement file"),
) -> UploadResponse:
    """Overwrite a file and delete every vector built from its old contents.

    Args:
        source_key: The object key to overwrite. Declared as a path parameter
            so keys containing slashes are matched whole.
        file: The replacement file. Its own name is ignored — the key is the
            one being replaced.

    Returns:
        The file's state after the write, and the number of vectors pruned.

    Raises:
        HTTPException: 400 if the replacement file is unacceptable.
    """
    data = await file.read()

    try:
        stored, pruned = await uploads.replace(source_key, data, file.filename or "")
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Built from what this call just did, not re-read from the index: a delete
    # takes a moment to propagate, and reading it back too soon would report
    # the file as stale when its vectors are in fact already gone.
    status = sync_status.build_status(stored, None, settings.embedding_model)

    return UploadResponse(status=status, replaced=True, pruned=pruned)


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
