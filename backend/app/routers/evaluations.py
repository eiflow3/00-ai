"""/evaluations endpoints — reading and withdrawing judgements.

Making a judgement belongs to the request it is about, so that lives on the
traces router. What is here is everything that treats judgements as a set: the
vocabulary they are written in, the list of them, withdrawal, and the export.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.docs.evaluations import (
    EVALUATIONS_TAG,
    EVALUATION_NOT_FOUND_RESPONSES,
    EVALUATION_OPTIONS_DESCRIPTION,
    EXPORT_DESCRIPTION,
    EXPORT_RESPONSES,
    LIST_EVALUATIONS_DESCRIPTION,
    RESTORE_EVALUATION_DESCRIPTION,
    WITHDRAW_EVALUATION_DESCRIPTION,
)
from app.schemas.evaluation import (
    Evaluation,
    EvaluationAuthor,
    EvaluationDeleteRequest,
    EvaluationOptions,
    EvaluationPage,
    EvaluationTarget,
    Verdict,
)
from app.services import evaluation_catalog, evaluation_export, evaluation_store

router = APIRouter(prefix="/evaluations", tags=[EVALUATIONS_TAG])

# Rows returned when a client names no page size.
DEFAULT_PAGE_SIZE = 50

# Content type for newline-delimited JSON, and the name the download lands under.
EXPORT_MEDIA_TYPE = "application/x-ndjson"
EXPORT_FILENAME = "evaluations.jsonl"


@router.get(
    "/options",
    response_model=EvaluationOptions,
    summary="List the verdicts and reason chips available",
    response_description="Every verdict, and every reason chip scoped to the stage it explains.",
    description=EVALUATION_OPTIONS_DESCRIPTION,
)
def evaluation_options() -> EvaluationOptions:
    """Return the vocabulary a client should build its evaluate control from.

    Returns:
        The verdicts, the reason chips, and the targets that may be judged.
    """
    return evaluation_catalog.options()


@router.get(
    "/export",
    summary="Download every judged request as JSONL",
    response_description="Newline-delimited JSON, one judged request per line.",
    description=EXPORT_DESCRIPTION,
    responses=EXPORT_RESPONSES,
)
async def export_evaluations() -> StreamingResponse:
    """Stream the judged requests as a JSONL file.

    Returns:
        A streaming response carrying one JSON object per judged request.
    """
    return StreamingResponse(
        evaluation_export.stream_jsonl(),
        media_type=EXPORT_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{EXPORT_FILENAME}"'},
    )


@router.get(
    "",
    response_model=EvaluationPage,
    summary="List judgements across every request",
    response_description="One page of judgements, newest first.",
    description=LIST_EVALUATIONS_DESCRIPTION,
)
async def list_evaluations(
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=evaluation_store.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, description="Rows to skip before this page"),
    target: Optional[EvaluationTarget] = Query(default=None, description="Only this stage"),
    verdict: Optional[Verdict] = Query(default=None, description="Only this verdict"),
    author: Optional[EvaluationAuthor] = Query(default=None, description="Only this author"),
    tag: Optional[str] = Query(default=None, description="Only judgements carrying this reason"),
    include_deleted: bool = Query(default=False, description="Include withdrawn judgements"),
) -> EvaluationPage:
    """Return one page of judgements.

    Returns:
        The page, with the total number of matching rows so a client can page.
    """
    return await evaluation_store.list_evaluations(
        limit=limit,
        offset=offset,
        target=target,
        verdict=verdict,
        author=author,
        tag=tag,
        include_deleted=include_deleted,
    )


@router.delete(
    "/{evaluation_id}",
    response_model=Evaluation,
    summary="Withdraw a judgement, keeping the record",
    response_description="The judgement as it now stands, marked withdrawn.",
    description=WITHDRAW_EVALUATION_DESCRIPTION,
    responses=EVALUATION_NOT_FOUND_RESPONSES,
)
async def withdraw_evaluation(
    evaluation_id: str, body: Optional[EvaluationDeleteRequest] = None
) -> Evaluation:
    """Mark one judgement withdrawn.

    Args:
        evaluation_id: The judgement to withdraw.
        body: Optionally, why it is being withdrawn.

    Returns:
        The judgement as it now stands.

    Raises:
        HTTPException: 404 when there is no judgement with that id.
    """
    evaluation = await evaluation_store.withdraw(
        evaluation_id, body.reason if body else ""
    )

    if evaluation is None:
        raise HTTPException(
            status_code=404, detail=f"No evaluation with id {evaluation_id!r}."
        )

    return evaluation


@router.post(
    "/{evaluation_id}/restore",
    response_model=Evaluation,
    summary="Reinstate a withdrawn judgement",
    response_description="The judgement as it now stands, no longer withdrawn.",
    description=RESTORE_EVALUATION_DESCRIPTION,
    responses=EVALUATION_NOT_FOUND_RESPONSES,
)
async def restore_evaluation(evaluation_id: str) -> Evaluation:
    """Clear a judgement's withdrawal marks.

    Args:
        evaluation_id: The judgement to reinstate.

    Returns:
        The judgement as it now stands.

    Raises:
        HTTPException: 404 when there is no judgement with that id.
    """
    evaluation = await evaluation_store.restore(evaluation_id)

    if evaluation is None:
        raise HTTPException(
            status_code=404, detail=f"No evaluation with id {evaluation_id!r}."
        )

    return evaluation
