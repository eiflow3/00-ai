"""/chunking endpoints — choosing, previewing and comparing ways to cut a file.

The experiment surface.  Where /sources puts a file into the index the app
answers from, these put the same file into as many isolated copies as you want
to compare, and then measure which copy retrieves best.

The router stays thin: it validates input, delegates to a service, and
serialises the result.  Which strategies exist, where a variant's vectors live,
and how a score is computed all live in app.services.
"""

import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from app.docs.chunking import (
    ATTACH_SCORE_DESCRIPTION,
    ATTACH_SCORE_RESPONSES,
    CHUNKING_TAG,
    DELETE_VARIANT_DESCRIPTION,
    LIST_VARIANTS_DESCRIPTION,
    POINT_PRODUCTION_DESCRIPTION,
    POINT_PRODUCTION_RESPONSES,
    PRODUCTION_DESCRIPTION,
    PREVIEW_DESCRIPTION,
    SCORE_DESCRIPTION,
    SCORE_RESPONSES,
    STOP_SCORE_DESCRIPTION,
    STOP_SCORE_RESPONSES,
    STRATEGIES_DESCRIPTION,
)
from app.schemas.chunking import (
    ChunkPreviewRequest,
    ChunkPreviewResponse,
    ChunkStrategySpec,
    ChunkVariant,
    ProductionSpace,
    ProductionSpaceRequest,
    VariantDeleteResponse,
)
from app.schemas.variant_score import (
    ScoreEnqueueResponse,
    ScoreRun,
    VariantScoreRequest,
)
from app.services import (
    answer_space,
    chunk_preview,
    chunk_variants,
    variant_score_queue,
)
from app.services.answer_space import UnusableSpace
from app.services.chunk_variants import UnknownVariant
from app.services.derived_artifacts import DerivedTextMissing
from app.services.chunking.base import UnknownStrategy
from app.services.chunking.catalog import catalog
from app.services.golden_store import UnknownGoldenSet
from app.services.text_extraction import UnsupportedSourceType
from app.services.variant_score_queue import ScoringRefused

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chunking", tags=[CHUNKING_TAG])


def _sse(cursor: int, event: object) -> dict:
    """Serialise one stream event into the dict sse-starlette expects.

    The cursor rides along as the SSE `id` field, which is what a client passes
    back as `after` when it re-attaches — so a reconnect resumes rather than
    replaying what it already has.
    """
    return {
        "id": str(cursor),
        "event": str(getattr(event, "event", "message")),
        "data": event.data.model_dump_json(),
    }


@router.get(
    "/strategies",
    response_model=list[ChunkStrategySpec],
    summary="List the ways a document can be cut",
    response_description="Every strategy on offer, described for a person choosing.",
    description=STRATEGIES_DESCRIPTION,
)
def list_strategies() -> list[ChunkStrategySpec]:
    """List every chunking strategy this deployment can run.

    Returns:
        One spec per strategy, in the order to present them.
    """
    return catalog()


@router.post(
    "/preview",
    response_model=ChunkPreviewResponse,
    summary="See how a strategy would cut a file",
    response_description="Every chunk the strategy produces, and the shape of the cut.",
    description=PREVIEW_DESCRIPTION,
    responses={
        400: {"description": "The geometry or the strategy cannot be run."},
        404: {"description": "No file at that key."},
        409: {"description": "The file's text is extracted at index time; index it first."},
        415: {"description": "No extractor handles this file type."},
    },
)
async def preview_chunking(body: ChunkPreviewRequest) -> ChunkPreviewResponse:
    """Cut a file with a strategy without embedding anything.

    Args:
        body: Which file to read, and how to cut it.

    Returns:
        The chunks and their summary statistics.

    Raises:
        HTTPException: 404 when the file is gone, 415 when it cannot be read,
            409 when its text has not been extracted yet, 400 when the
            strategy or geometry cannot produce chunks.
    """
    try:
        return await chunk_preview.preview(body.source_key, body.config)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No source at key: {body.source_key}")
    except UnsupportedSourceType as exc:
        raise HTTPException(status_code=415, detail=str(exc))
    except DerivedTextMissing as exc:
        # The file exists and is supported, but its text is produced during
        # indexing — a preview cannot afford to run that extraction inline.
        raise HTTPException(status_code=409, detail=str(exc))
    except (UnknownStrategy, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/variants",
    response_model=list[ChunkVariant],
    summary="List the chunking variants that hold vectors",
    response_description="Every variant currently embedded, newest first.",
    description=LIST_VARIANTS_DESCRIPTION,
)
async def list_variants() -> list[ChunkVariant]:
    """List every variant that currently holds vectors.

    Returns:
        The variants, most recently embedded first.
    """
    return await chunk_variants.list_variants()


@router.post(
    "/score",
    response_model=ScoreEnqueueResponse,
    status_code=202,
    summary="Score every variant against a golden set",
    response_description="The run to stream, and what it will measure.",
    description=SCORE_DESCRIPTION,
    responses=SCORE_RESPONSES,
)
async def score_variants(body: VariantScoreRequest) -> ScoreEnqueueResponse:
    """Start a comparison run and return the id to follow it by.

    Args:
        body: Which golden set, which variants, and whether to generate answers.

    Returns:
        The run id, the variants it covers, and how many questions each faces.

    Raises:
        HTTPException: 404 when the golden set does not exist, 400 when there
            is nothing to compare or the provider cannot be used.
    """
    try:
        return await variant_score_queue.enqueue(body)
    except UnknownGoldenSet as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ScoringRefused, UnknownVariant, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/score/{job_id}/events",
    summary="Stream one scoring run's progress",
    response_description="SSE stream of the run's events, ending with the ranking.",
    description=ATTACH_SCORE_DESCRIPTION,
    responses=ATTACH_SCORE_RESPONSES,
)
async def stream_score(
    job_id: str,
    after: int = Query(
        default=-1,
        description="Cursor already seen; everything past it is replayed first",
    ),
):
    """Follow one scoring run's events, replaying what the client missed.

    Args:
        job_id: The run to follow.
        after: Cursor already seen. The default replays from the beginning.
    """

    async def event_generator() -> AsyncIterator[dict]:
        """Serialise each event the run yields."""
        async for cursor, event in variant_score_queue.subscribe(job_id, after):
            yield _sse(cursor, event)

    return EventSourceResponse(event_generator())


@router.delete(
    "/score/{job_id}",
    response_model=ScoreRun,
    summary="Stop a scoring run",
    response_description="The run as it stands after being stopped.",
    description=STOP_SCORE_DESCRIPTION,
    responses=STOP_SCORE_RESPONSES,
)
async def stop_score(job_id: str) -> ScoreRun:
    """Stop a scoring run, keeping what it already measured.

    Args:
        job_id: The run to stop.

    Returns:
        The run's record after stopping.

    Raises:
        HTTPException: 404 when no such run is still in memory.
    """
    await variant_score_queue.cancel(job_id)

    run = variant_score_queue.get(job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No such scoring run: {job_id}")

    return run


@router.get(
    "/production",
    response_model=ProductionSpace,
    summary="Report which vector space answers by default",
    response_description="The space production points at, and whether it can answer.",
    description=PRODUCTION_DESCRIPTION,
)
async def get_production() -> ProductionSpace:
    """Report where the application answers questions from.

    Returns:
        The pointer, with the space's live vector count and file list.
    """
    return await answer_space.describe()


@router.put(
    "/production",
    response_model=ProductionSpace,
    summary="Point production at a different vector space",
    response_description="Where production now answers from.",
    description=POINT_PRODUCTION_DESCRIPTION,
    responses=POINT_PRODUCTION_RESPONSES,
)
async def point_production(body: ProductionSpaceRequest) -> ProductionSpace:
    """Adopt one variant as the space every ungrounded question reads.

    Args:
        body: The variant to answer from, or empty for the original index.

    Returns:
        Where production now answers from.

    Raises:
        HTTPException: 400 when the id is not one this app can run, 409 when
            the space it names cannot answer — empty, or holding an incomplete
            copy of a file.
    """
    try:
        return await answer_space.point_at(body.variant_id)
    except UnknownVariant as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UnusableSpace as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# Declared last: `{variant_id}` would otherwise swallow the score routes above,
# and Starlette matches in declaration order.
@router.delete(
    "/variants/{variant_id}",
    response_model=VariantDeleteResponse,
    summary="Delete a variant and its vectors",
    response_description="How many vectors were removed.",
    description=DELETE_VARIANT_DESCRIPTION,
    responses={
        400: {"description": "Not a variant this app can run."},
        409: {"description": "This is the space production answers from."},
    },
)
async def delete_variant(variant_id: str) -> VariantDeleteResponse:
    """Drop one variant's vectors, leaving the source files alone.

    Args:
        variant_id: The variant to remove.

    Returns:
        The variant, and how many vectors were deleted.

    Raises:
        HTTPException: 400 when the id is not one this app can run, 409 when it
            is the space production answers from.
    """
    # Refused rather than followed by a silent fallback: deleting what every
    # question is answered from is a decision, and pointing production
    # somewhere else first is how it gets made.
    if variant_id == await answer_space.current():
        raise HTTPException(
            status_code=409,
            detail=(
                f"{variant_id} is the space production answers from. Point "
                "production somewhere else before deleting it."
            ),
        )

    try:
        deleted = await chunk_variants.delete(variant_id)
    except UnknownVariant as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return VariantDeleteResponse(variant_id=variant_id, deleted=deleted)
