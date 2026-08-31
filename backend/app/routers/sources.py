"""/sources endpoints — the data embedding pipeline's HTTP surface.

The counterpart to the chat router: where that one answers questions from the
index, these manage what goes into it.  The router stays thin — it validates
input, delegates each operation to a service, and serialises the result.  The
storage-versus-index comparison, the chunking, and the embedding all live in
app.services.
"""

import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.docs.sources import (
    ATTACH_RUN_DESCRIPTION,
    ATTACH_RUN_RESPONSES,
    DEINDEX_DESCRIPTION,
    DELETE_RESPONSES,
    DELETE_SOURCE_DESCRIPTION,
    GET_SOURCE_DESCRIPTION,
    GET_SOURCE_RESPONSES,
    INDEX_SOURCES_DESCRIPTION,
    INDEX_SOURCES_RESPONSES,
    LIST_RUNS_DESCRIPTION,
    LIST_SOURCES_DESCRIPTION,
    REPLACE_DESCRIPTION,
    REPLACE_RESPONSES,
    SOURCES_TAG,
    STOP_RUN_DESCRIPTION,
    STOP_RUN_RESPONSES,
    UPLOAD_DESCRIPTION,
    UPLOAD_RESPONSES,
)
from app.schemas.ingestion import EnqueueResponse, IndexRequest, IndexRun
from app.schemas.source import (
    DeindexResponse,
    DeleteResponse,
    IndexState,
    SourceDetail,
    SourceStatus,
    UploadResponse,
)
from app.services import deletion, index_queue, sync_status, uploads
from app.services.deletion import DeletionBlocked
from app.services.uploads import UploadRejected

logger = logging.getLogger(__name__)

# How many recent runs the runs listing returns.
RECENT_RUNS_LIMIT = 10

router = APIRouter(prefix="/sources", tags=[SOURCES_TAG])


def _sse(cursor: int, event: object) -> dict:
    """Serialise one stream event into the dict sse-starlette expects.

    The cursor rides along as the SSE `id` field, which is what a client passes
    back as `after` when it re-attaches — so a reconnect resumes rather than
    replaying everything it already has.

    Two shapes arrive here. A live event is a schema model, so its payload is
    dumped from the same models the OpenAPI docs describe. An event replayed
    from run history is already serialised JSON, and re-parsing it only to dump
    it again would be work for no gain.
    """
    payload = getattr(event, "payload", None)
    if payload is None:
        payload = event.data.model_dump_json()

    return {"id": str(cursor), "event": str(event.event), "data": payload}


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
    response_model=EnqueueResponse,
    status_code=202,
    summary="Queue files for embedding",
    response_description="The run the files joined, and what was accepted.",
    description=INDEX_SOURCES_DESCRIPTION,
    responses=INDEX_SOURCES_RESPONSES,
)
async def index_sources(body: IndexRequest) -> EnqueueResponse:
    """Queue files for embedding and return the run they joined.

    Deliberately does not stream. The work used to be the response itself,
    which meant a client reloading cancelled it mid-file; now the request only
    enqueues, and progress is read from the run's own stream. That also puts
    validation back where it belongs — once an SSE response opens, its status
    code has already been sent and a bad request cannot be reported.

    Args:
        body: Which files to index, and how to chunk and embed them.

    Returns:
        The run id to stream from, plus what was accepted, already queued,
        refused for the queue limit, or missing from storage.

    Raises:
        HTTPException: 400 if the chunk geometry cannot make progress.
    """
    if body.chunk_overlap >= body.chunk_size:
        raise HTTPException(
            status_code=400,
            detail=(
                f"chunk_overlap ({body.chunk_overlap}) must be smaller than "
                f"chunk_size ({body.chunk_size}); otherwise chunking cannot advance."
            ),
        )

    return await index_queue.enqueue(body)


@router.get(
    "/index/runs",
    response_model=list[IndexRun],
    summary="List indexing runs, live and recent",
    response_description="The run in flight, if any, followed by recent history.",
    description=LIST_RUNS_DESCRIPTION,
)
async def list_runs() -> list[IndexRun]:
    """List the run in flight and recent finished ones.

    This is what a client asks on load: if a run is in flight it holds the id
    needed to attach to its stream, which is how progress survives a reload.

    Returns:
        The live run first, then history newest-first.
    """
    return await index_queue.recent(RECENT_RUNS_LIMIT)


@router.get(
    "/index/runs/{job_id}/events",
    summary="Stream one run's progress",
    response_description="SSE stream of the run's events, ending with a summary.",
    description=ATTACH_RUN_DESCRIPTION,
    responses=ATTACH_RUN_RESPONSES,
)
async def stream_run(
    job_id: str,
    after: int = Query(
        default=-1,
        description="Cursor already seen; everything past it is replayed first",
    ),
):
    """Follow one run's events, replaying what the client missed.

    The only streaming endpoint here, used identically for a run just started
    and one being returned to — so there is one framing path rather than two
    that have to behave the same.

    Args:
        job_id: The run to follow.
        after: Cursor already seen. The default replays from the beginning,
            which is what a client that just reloaded wants.
    """

    async def event_generator() -> AsyncIterator[dict]:
        """Serialise each event the run yields."""
        async for cursor, event in index_queue.subscribe(job_id, after):
            yield _sse(cursor, event)

    return EventSourceResponse(event_generator())


@router.delete(
    "/index/runs/{job_id}",
    response_model=IndexRun,
    summary="Stop a run and clear its queue",
    response_description="The run as it stands after being stopped.",
    description=STOP_RUN_DESCRIPTION,
    responses=STOP_RUN_RESPONSES,
)
async def stop_run(job_id: str) -> IndexRun:
    """Stop a run, discarding whatever was still waiting.

    Needed because a run no longer dies with the tab that started it: without
    this, a large run begun by mistake could only be waited out.

    Args:
        job_id: The run to stop.

    Returns:
        The run's record after stopping.

    Raises:
        HTTPException: 404 when no such run is still in memory.
    """
    await index_queue.cancel(job_id)

    run = index_queue.get(job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No such run: {job_id}")

    return run


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

    Raises:
        HTTPException: 409 while an indexing run is holding the file.
    """
    try:
        deleted = await deletion.delete_vectors(source_key)
    except DeletionBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return DeindexResponse(source_key=source_key, deleted=deleted)


# Declared last on purpose. `{source_key:path}` swallows anything, so every
# more specific route above — the run endpoints included — has to be matched
# first, and Starlette matches in declaration order.
@router.delete(
    "/{source_key:path}",
    response_model=DeleteResponse,
    summary="Delete a file and every vector built from it",
    response_description="What was removed from each side.",
    description=DELETE_SOURCE_DESCRIPTION,
    responses=DELETE_RESPONSES,
)
async def delete_source(source_key: str) -> DeleteResponse:
    """Remove a file from storage along with its embeddings.

    The hard counterpart to deindexing, which keeps the file. Deleting a key
    that is already gone from both sides is reported rather than raised — the
    caller asked for a state the store is already in.

    Args:
        source_key: The object key to delete.

    Returns:
        How many vectors were removed, and whether a file was there to remove.

    Raises:
        HTTPException: 409 while an indexing run is holding the file.
    """
    try:
        deleted, removed = await deletion.delete_source(source_key)
    except DeletionBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return DeleteResponse(
        source_key=source_key, vectors_deleted=deleted, file_deleted=removed
    )
