"""HTTP surface for generating and reviewing golden sets.

Validate, delegate, serialise. Every decision worth making happens in
`app.services.golden_*`; what is left here is the mapping from a service
exception to a status code, and the two streaming responses.
"""

from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from app.docs.golden import (
    DELETE_SET_DESCRIPTION,
    EXPORT_SET_DESCRIPTION,
    EXPORT_SET_RESPONSES,
    GET_RUN_DESCRIPTION,
    GET_SET_DESCRIPTION,
    GOLDEN_TAG,
    LIST_OPTIONS_DESCRIPTION,
    LIST_SETS_DESCRIPTION,
    RESTORE_SET_DESCRIPTION,
    ROW_NOT_FOUND_RESPONSES,
    RUN_STREAM_DESCRIPTION,
    RUN_STREAM_RESPONSES,
    SET_NOT_FOUND_RESPONSES,
    START_RUN_DESCRIPTION,
    START_RUN_RESPONSES,
    STOP_RUN_DESCRIPTION,
    STOP_RUN_RESPONSES,
    UPDATE_ROW_DESCRIPTION,
    UPDATE_SET_DESCRIPTION,
)
from app.schemas.golden import (
    GoldenEnqueueResponse,
    GoldenOptions,
    GoldenRow,
    GoldenRowUpdate,
    GoldenRun,
    GoldenRunRequest,
    GoldenSet,
    GoldenSetDetail,
)
from app.services import golden_catalog, golden_export, golden_queue, golden_store
from app.services.golden_store import UnknownGoldenRow, UnknownGoldenSet

router = APIRouter(prefix="/golden", tags=[GOLDEN_TAG])

# Media type for newline-delimited JSON, which is what the harness reads.
NDJSON_MEDIA_TYPE = "application/x-ndjson"


def _sse(cursor: int, event: object) -> dict:
    """Serialise one stream event into the dict sse-starlette expects.

    The cursor rides along as the SSE `id` field, which is what a client passes
    back as `after` when it re-attaches — so a reconnect resumes rather than
    replaying everything it already has.
    """
    return {"id": str(cursor), "event": str(event.event), "data": event.data.model_dump_json()}


@router.get(
    "/options",
    response_model=GoldenOptions,
    summary="List the question types, difficulties and checks a set is built from",
    response_description="Every value a client may display or filter by.",
    description=LIST_OPTIONS_DESCRIPTION,
)
async def list_options() -> GoldenOptions:
    """Serve the closed vocabulary rather than have clients guess it.

    Returns:
        Every question type, difficulty, and validator check.
    """
    return GoldenOptions(
        types=golden_catalog.types(),
        difficulties=golden_catalog.difficulties(),
        checks=golden_catalog.checks(),
    )


@router.post(
    "/runs",
    response_model=GoldenEnqueueResponse,
    status_code=202,
    summary="Start drafting a golden set from a source file",
    response_description="The run to follow, and the set it will fill.",
    description=START_RUN_DESCRIPTION,
    responses=START_RUN_RESPONSES,
)
async def start_run(request: GoldenRunRequest) -> GoldenEnqueueResponse:
    """Start a generation run.

    Args:
        request: What to draft from, and with which model.

    Returns:
        The run's job id and the id of the set it will fill.

    Raises:
        HTTPException: 404 when no file exists at that key, 400 when the file
            is unreadable or the provider is not one this deployment offers.
    """
    try:
        return await golden_queue.enqueue(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/runs/{job_id}",
    response_model=GoldenRun,
    summary="Where a generation run stands",
    response_description="The run's state and progress.",
    description=GET_RUN_DESCRIPTION,
    responses=STOP_RUN_RESPONSES,
)
async def get_run(job_id: str) -> GoldenRun:
    """Report a run without opening its stream.

    Args:
        job_id: The run to read.

    Returns:
        The run's record.

    Raises:
        HTTPException: 404 when no such run is still in memory.
    """
    run = golden_queue.get(job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No such run: {job_id}")
    return run


@router.get(
    "/runs/{job_id}/stream",
    summary="Follow a generation run as it drafts",
    response_description="A server-sent event stream of the run's progress.",
    description=RUN_STREAM_DESCRIPTION,
    responses=RUN_STREAM_RESPONSES,
)
async def stream_run(
    job_id: str,
    after: int = Query(
        default=-1,
        description="Cursor already seen; everything past it is replayed, then the stream follows live",
    ),
):
    """Stream one run's events, resuming from a cursor.

    Args:
        job_id: The run to follow.
        after: Last cursor the client saw.

    Returns:
        The event stream.
    """

    async def event_generator() -> AsyncIterator[dict]:
        """Serialise each event the run yields."""
        async for cursor, event in golden_queue.subscribe(job_id, after):
            yield _sse(cursor, event)

    return EventSourceResponse(event_generator())


@router.delete(
    "/runs/{job_id}",
    response_model=GoldenRun,
    summary="Stop a generation run",
    response_description="The run as it stands after being stopped.",
    description=STOP_RUN_DESCRIPTION,
    responses=STOP_RUN_RESPONSES,
)
async def stop_run(job_id: str) -> GoldenRun:
    """Stop a run in flight.

    Args:
        job_id: The run to stop.

    Returns:
        The run's record after stopping.

    Raises:
        HTTPException: 404 when no such run is still in memory.
    """
    await golden_queue.cancel(job_id)

    run = golden_queue.get(job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No such run: {job_id}")
    return run


@router.get(
    "/sets",
    response_model=list[GoldenSet],
    summary="List every golden set",
    response_description="Each set with its row counts, newest first.",
    description=LIST_SETS_DESCRIPTION,
)
async def list_sets(
    include_deleted: bool = Query(default=False, description="Include withdrawn sets"),
) -> list[GoldenSet]:
    """List the sets.

    Args:
        include_deleted: Whether withdrawn sets are included.

    Returns:
        The sets, newest first.
    """
    return await golden_store.list_sets(include_deleted=include_deleted)


@router.get(
    "/sets/{set_id}",
    response_model=GoldenSetDetail,
    summary="Read one golden set and all its rows",
    response_description="The set, its rows, and the validator's findings.",
    description=GET_SET_DESCRIPTION,
    responses=SET_NOT_FOUND_RESPONSES,
)
async def get_set(set_id: str) -> GoldenSetDetail:
    """Read one set in full.

    Args:
        set_id: Set to read.

    Returns:
        The set and its rows.

    Raises:
        HTTPException: 404 when no set has that id.
    """
    try:
        return await golden_store.get(set_id)
    except UnknownGoldenSet as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch(
    "/sets/{set_id}",
    response_model=GoldenSetDetail,
    summary="Rename the file a set exports as",
    response_description="The set with its new slug.",
    description=UPDATE_SET_DESCRIPTION,
    responses=SET_NOT_FOUND_RESPONSES,
)
async def rename_set(
    set_id: str,
    slug: str = Query(..., min_length=1, max_length=120, description="New filename stem"),
) -> GoldenSetDetail:
    """Rename a set's exported file.

    Args:
        set_id: Set to rename.
        slug: New filename stem.

    Returns:
        The set as it now stands.

    Raises:
        HTTPException: 404 when no set has that id.
    """
    try:
        await golden_store.get(set_id)
        await golden_store.set_slug(set_id, slug)
        return await golden_store.get(set_id)
    except UnknownGoldenSet as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch(
    "/sets/{set_id}/rows/{row_id}",
    response_model=GoldenRow,
    summary="Edit a row, or record a decision about it",
    response_description="The row as it now stands, re-checked if its content moved.",
    description=UPDATE_ROW_DESCRIPTION,
    responses=ROW_NOT_FOUND_RESPONSES,
)
async def update_row(set_id: str, row_id: str, update: GoldenRowUpdate) -> GoldenRow:
    """Apply an edit or a review decision to one row.

    Args:
        set_id: Set the row belongs to.
        row_id: Row to change.
        update: The fields to change.

    Returns:
        The row, with a fresh verdict when its content changed.

    Raises:
        HTTPException: 404 when the set, the row, or the source file is gone,
            400 when the source file can no longer be read to re-check against.
    """
    try:
        return await golden_queue.apply_row_update(set_id, row_id, update)
    except (UnknownGoldenSet, UnknownGoldenRow, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/sets/{set_id}/export",
    summary="Download a set as the JSONL the harness reads",
    response_description="Newline-delimited JSON, one row per line.",
    description=EXPORT_SET_DESCRIPTION,
    responses=EXPORT_SET_RESPONSES,
)
async def export_set(set_id: str):
    """Stream a set as a downloadable JSONL file.

    Args:
        set_id: Set to export.

    Returns:
        The file, named after the set's slug.

    Raises:
        HTTPException: 404 when no set has that id.
    """
    try:
        golden_set = await golden_store.get(set_id)
    except UnknownGoldenSet as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    filename = f"{golden_set.slug}.jsonl"
    return StreamingResponse(
        golden_export.stream_jsonl(golden_set.rows),
        media_type=NDJSON_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete(
    "/sets/{set_id}",
    status_code=204,
    summary="Withdraw a golden set",
    description=DELETE_SET_DESCRIPTION,
    responses=SET_NOT_FOUND_RESPONSES,
)
async def delete_set(set_id: str) -> None:
    """Soft-delete a set.

    Args:
        set_id: Set to withdraw.

    Raises:
        HTTPException: 404 when no set has that id.
    """
    try:
        await golden_store.withdraw(set_id)
    except UnknownGoldenSet as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/sets/{set_id}/restore",
    response_model=GoldenSetDetail,
    summary="Undo a withdrawal",
    response_description="The set, back in the listing.",
    description=RESTORE_SET_DESCRIPTION,
    responses=SET_NOT_FOUND_RESPONSES,
)
async def restore_set(set_id: str) -> GoldenSetDetail:
    """Restore a withdrawn set.

    Args:
        set_id: Set to restore.

    Returns:
        The set as it now stands.

    Raises:
        HTTPException: 404 when no set has that id.
    """
    try:
        await golden_store.restore(set_id)
        return await golden_store.get(set_id)
    except UnknownGoldenSet as exc:
        raise HTTPException(status_code=404, detail=str(exc))
