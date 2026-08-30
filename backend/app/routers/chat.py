"""POST /chat endpoint — runs retrieval, then streams the LLM answer via SSE.

The router stays thin on purpose: it validates input, delegates each pipeline
stage to a service, and serialises the results as SSE events. Retrieval,
prompt construction, provider calls, and pricing all live in app.services.
"""

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.docs.chat import CHAT_DESCRIPTION, CHAT_MODELS_DESCRIPTION, CHAT_RESPONSES
from app.schemas.chat import (
    ChatRequest,
    ModelOption,
    ChatStreamErrorEvent,
    ChatStreamRetrievalEvent,
    ChatStreamUsageEvent,
    ErrorEventData,
    RetrievalEventData,
)
from app.schemas.retrieval import RetrievalResult
from app.services.cost_tracker import calculate_cost
from app.services.llm import get_adapter
from app.services.llm.catalog import DEFAULT_MODELS, list_models
from app.services.prompt_builder import build_messages
from app.services.retrieval import retrieve

router = APIRouter()

# Fallback when a provider has no default in the catalog.
_FALLBACK_MODEL = "gpt-5.6-terra"


def _sse(event: ChatStreamRetrievalEvent | ChatStreamErrorEvent | ChatStreamUsageEvent) -> dict:
    """Serialise a typed stream event into the dict sse-starlette expects.

    Going through the schema models keeps every event's payload defined in
    one place — the same models the OpenAPI docs describe.
    """
    payload = event.data
    # Retrieval and error payloads are pydantic models; the usage payload is
    # the cost tracker's dataclass, which rounds its own floats in to_dict().
    data = (
        payload.model_dump_json()
        if isinstance(payload, BaseModel)
        else json.dumps(payload.to_dict())
    )
    return {"event": event.event, "data": data}


@router.get(
    "/chat/models",
    response_model=list[ModelOption],
    summary="List the providers and models this deployment offers",
    response_description="Every provider/model pair, and whether it is usable here.",
    description=CHAT_MODELS_DESCRIPTION,
)
def chat_models() -> list[ModelOption]:
    """List the provider/model pairs a client may choose between.

    Returns:
        One entry per pair, carrying display labels, whether this deployment
        has the credentials for it, and any caveat worth showing.
    """
    return [ModelOption(**entry) for entry in list_models()]


@router.post(
    "/chat",
    summary="Stream a retrieval-grounded chat response",
    response_description="SSE stream of retrieval results followed by the generated answer.",
    description=CHAT_DESCRIPTION,
    responses=CHAT_RESPONSES,
)
async def chat(body: ChatRequest):
    """Retrieve relevant context, then stream a grounded chat response.

    1. Resolves the correct adapter via the factory.
    2. Runs the retrieval pipeline (embed query -> vector search -> rank).
    3. Emits a "retrieval" event with each chunk and its similarity score.
    4. Streams the LLM's text deltas, grounded in those chunks.
    5. Sends a final "usage" event with token counts and cost breakdown.
    """
    # Get the adapter for the requested provider.
    try:
        adapter = get_adapter(body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Resolve the model — use the client's override or fall back to the default.
    model = body.model or DEFAULT_MODELS.get(body.provider, _FALLBACK_MODEL)

    async def event_generator() -> AsyncIterator[dict]:
        """Yield the retrieval event, the text deltas, then the usage event."""
        # --- Retrieval phase ------------------------------------------------
        # Client-supplied chunks win, so a caller that already ran retrieval
        # (or is replaying a conversation) isn't charged for it twice.
        if body.context_chunks:
            result = RetrievalResult(
                query=body.query,
                chunks=body.context_chunks,
                total_searched=len(body.context_chunks),
            )
        elif body.use_rag:
            try:
                result = await retrieve(
                    body.query,
                    top_k=body.top_k,
                    score_threshold=body.score_threshold,
                    embedding_model=body.embedding_model,
                )
            except Exception as exc:
                # Retrieval is best-effort: tell the client it failed, then
                # answer without context rather than dropping the request.
                yield _sse(ChatStreamErrorEvent(
                    data=ErrorEventData(stage="retrieval", message=str(exc))
                ))
                result = RetrievalResult(query=body.query)
        else:
            result = RetrievalResult(query=body.query)

        # Emit the retrieved chunks — with scores — before any generated text,
        # so the client can render citations while the answer streams in.
        yield _sse(ChatStreamRetrievalEvent(data=RetrievalEventData(
            query=result.query,
            chunks=result.chunks,
            total_searched=result.total_searched,
            embedding_model=body.embedding_model,
        )))

        # --- Generation phase -----------------------------------------------
        messages = build_messages(
            query=body.query,
            chunks=result.chunks,
            system_prompt=body.system_prompt,
        )

        # Stream text deltas from the LLM adapter. The response status was
        # sent when the stream opened, so a provider failure here cannot become
        # an HTTP error — it has to be an event, or the connection just dies
        # mid-answer with nothing explaining why.
        try:
            async for token in adapter.stream(messages, model, body.temperature):
                # Each yield sends a "data: <token>\n\n" SSE event to the client.
                yield {"data": token}
        except Exception as exc:
            yield _sse(ChatStreamErrorEvent(
                data=ErrorEventData(stage="generation", message=str(exc))
            ))
            return

        # After streaming completes, calculate and send cost breakdown.
        # The adapter populates self.usage with token counts after the stream.
        if adapter.usage:
            cost = calculate_cost(model, adapter.usage)
            # Send a separate "usage" event so the client can distinguish
            # cost metadata from the text stream.
            yield _sse(ChatStreamUsageEvent(data=cost))

    # Return the SSE streaming response.
    # media_type ensures the browser/client treats this as an event stream.
    return EventSourceResponse(event_generator())
