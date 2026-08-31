"""/prompts endpoints — reading and editing the pipeline's prompt templates.

The router stays thin: it validates input, delegates to `prompt_store`, and
turns the store's two failure modes into the status codes that describe them —
an unknown id is a 404, an unrenderable template is a 400.
"""

from fastapi import APIRouter, HTTPException

from app.docs.prompts import (
    GET_PROMPT_DESCRIPTION,
    INVALID_TEMPLATE_RESPONSES,
    LIST_PROMPTS_DESCRIPTION,
    PREVIEW_PROMPTS_DESCRIPTION,
    PROMPTS_TAG,
    PROMPT_NOT_FOUND_RESPONSES,
    RESET_PROMPT_DESCRIPTION,
    UPDATE_PROMPT_DESCRIPTION,
)
from app.schemas.prompt import (
    Prompt,
    PromptPreview,
    PromptPreviewRequest,
    PromptUpdateRequest,
)
from app.services import prompt_store
from app.services.prompt_catalog import InvalidTemplate, UnknownPrompt

router = APIRouter(prefix="/prompts", tags=[PROMPTS_TAG])


@router.get(
    "",
    response_model=list[Prompt],
    summary="List every prompt the pipeline is assembled from",
    response_description="Each prompt, its default, and the variables it may use.",
    description=LIST_PROMPTS_DESCRIPTION,
)
async def list_prompts() -> list[Prompt]:
    """Return every prompt in the order a request assembles them.

    Returns:
        The prompts, each carrying the text in force and its shipped default.
    """
    return await prompt_store.list_prompts()


@router.post(
    "/preview",
    response_model=PromptPreview,
    summary="Render the prompts in force into the messages they produce",
    response_description="The assembled message list, in the order it is sent.",
    description=PREVIEW_PROMPTS_DESCRIPTION,
    responses=INVALID_TEMPLATE_RESPONSES,
)
async def preview_prompts(body: PromptPreviewRequest) -> PromptPreview:
    """Assemble a sample request from the prompts currently in force.

    Returns:
        The messages an adapter would receive, fully interpolated.

    Raises:
        HTTPException: 400 if a stored template no longer renders.
    """
    try:
        return await prompt_store.preview(body)
    except InvalidTemplate as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/{prompt_id}",
    response_model=Prompt,
    summary="Fetch one prompt",
    response_description="The prompt as it stands, with the default behind it.",
    description=GET_PROMPT_DESCRIPTION,
    responses=PROMPT_NOT_FOUND_RESPONSES,
)
async def get_prompt(prompt_id: str) -> Prompt:
    """Return one prompt as it currently stands.

    Raises:
        HTTPException: 404 if no prompt carries this id.
    """
    try:
        return await prompt_store.get(prompt_id)
    except UnknownPrompt as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put(
    "/{prompt_id}",
    response_model=Prompt,
    summary="Replace one prompt's text",
    response_description="The prompt as it now stands.",
    description=UPDATE_PROMPT_DESCRIPTION,
    responses=INVALID_TEMPLATE_RESPONSES,
)
async def update_prompt(prompt_id: str, body: PromptUpdateRequest) -> Prompt:
    """Save an override for one prompt.

    Raises:
        HTTPException: 404 if no prompt carries this id, 400 if the template
            would not render in the pipeline.
    """
    try:
        return await prompt_store.save(prompt_id, body.template)
    except UnknownPrompt as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTemplate as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/{prompt_id}/reset",
    response_model=Prompt,
    summary="Restore one prompt to the text it ships with",
    response_description="The prompt, back on its default.",
    description=RESET_PROMPT_DESCRIPTION,
    responses=PROMPT_NOT_FOUND_RESPONSES,
)
async def reset_prompt(prompt_id: str) -> Prompt:
    """Discard one prompt's override.

    Raises:
        HTTPException: 404 if no prompt carries this id.
    """
    try:
        return await prompt_store.reset(prompt_id)
    except UnknownPrompt as exc:
        raise HTTPException(status_code=404, detail=str(exc))
