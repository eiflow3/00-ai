"""/traces endpoints — the recorded evidence behind each answer.

The router stays thin: it validates input, delegates to a service, and
serialises the result.  What a trace is, and why its chunks are stored verbatim,
lives in app.services.trace_store.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.docs.traces import (
    CREATE_EVALUATION_DESCRIPTION,
    CREATE_EVALUATION_RESPONSES,
    DELETE_TRACE_DESCRIPTION,
    GET_TRACE_DESCRIPTION,
    GET_TRACE_RESPONSES,
    LIST_TRACES_DESCRIPTION,
    TRACES_TAG,
)
from app.schemas.evaluation import (
    Evaluation,
    EvaluationRequest,
    EvaluationTarget,
    Verdict,
)
from app.schemas.trace import TraceDeleteResponse, TraceDetail, TracePage, TraceState
from app.services import evaluation_store, trace_store
from app.services.evaluation_store import TraceNotFound

router = APIRouter(prefix="/traces", tags=[TRACES_TAG])

# Rows returned when a client names no page size.
DEFAULT_PAGE_SIZE = 50


@router.get(
    "",
    response_model=TracePage,
    summary="List recorded chat requests",
    response_description="One page of traces, newest first, each with its verdict rollup.",
    description=LIST_TRACES_DESCRIPTION,
)
async def list_traces(
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=trace_store.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, description="Rows to skip before this page"),
    model: Optional[str] = Query(default=None, description="Only this model's answers"),
    state: Optional[TraceState] = Query(default=None, description="Only requests that ended this way"),
    evaluated: Optional[bool] = Query(default=None, description="Judged only, or unjudged only"),
    verdict: Optional[Verdict] = Query(default=None, description="Only requests carrying this live verdict"),
    target: Optional[EvaluationTarget] = Query(default=None, description="Only requests with this stage judged"),
    source_key: Optional[str] = Query(default=None, description="Only requests that retrieved this file"),
    search: Optional[str] = Query(default=None, description="Substring match on the question"),
) -> TracePage:
    """Return one page of recorded requests.

    Returns:
        The page, with the total number of matching rows so a client can page.
    """
    return await trace_store.list_traces(
        limit=limit,
        offset=offset,
        model=model,
        state=state,
        evaluated=evaluated,
        verdict=verdict,
        target=target,
        source_key=source_key,
        search=search,
    )


@router.get(
    "/models",
    response_model=list[str],
    summary="List the models that have answered",
    response_description="Every distinct model present in the trace history.",
)
async def trace_models() -> list[str]:
    """Return every model with at least one recorded answer.

    Returns:
        The model ids, so a client's filter offers only what exists.
    """
    return await trace_store.distinct_models()


@router.get(
    "/{trace_id}",
    response_model=TraceDetail,
    summary="Get one request with its chunks and judgements",
    response_description="The request, the chunks that grounded it, and every judgement made on it.",
    description=GET_TRACE_DESCRIPTION,
    responses=GET_TRACE_RESPONSES,
)
async def get_trace(trace_id: str) -> TraceDetail:
    """Return one recorded request in full.

    Args:
        trace_id: The trace to load.

    Returns:
        The request with its chunks and judgements.

    Raises:
        HTTPException: 404 when no trace was recorded under that id.
    """
    detail = await trace_store.get_detail(trace_id)

    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"No trace recorded under id {trace_id!r}."
        )

    return detail


@router.delete(
    "/{trace_id}",
    response_model=TraceDeleteResponse,
    summary="Discard a recorded request entirely",
    response_description="Whether a trace was removed.",
    description=DELETE_TRACE_DESCRIPTION,
)
async def delete_trace(trace_id: str) -> TraceDeleteResponse:
    """Remove one request, its chunks and its judgements.

    Args:
        trace_id: The trace to discard.

    Returns:
        The id, and whether anything was actually removed. A trace that was
        already gone is not an error — it is the end state the caller asked for.
    """
    deleted = await trace_store.delete_trace(trace_id)
    return TraceDeleteResponse(trace_id=trace_id, deleted=deleted)


@router.post(
    "/{trace_id}/evaluations",
    response_model=Evaluation,
    status_code=201,
    summary="Judge one stage of a recorded request",
    response_description="The judgement as stored.",
    description=CREATE_EVALUATION_DESCRIPTION,
    responses=CREATE_EVALUATION_RESPONSES,
)
async def create_evaluation(trace_id: str, body: EvaluationRequest) -> Evaluation:
    """Record a verdict on one stage of one request.

    Args:
        trace_id: The request being judged.
        body: The verdict, its target, and any reasons given.

    Returns:
        The stored judgement.

    Raises:
        HTTPException: 404 when the trace is unknown, 400 when a tag is not
            valid for the stage being judged.
    """
    try:
        return await evaluation_store.create(trace_id, body)
    except TraceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        # A tag that does not exist, or belongs to the other stage — a client
        # bug, and one worth reporting rather than silently dropping.
        raise HTTPException(status_code=400, detail=str(exc))
