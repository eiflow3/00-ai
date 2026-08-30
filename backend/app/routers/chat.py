"""POST /chat endpoint — streams LLM responses via Server-Sent Events."""

import json

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.schemas.chat import ChatRequest
from app.services.llm import get_adapter
from app.services.cost_tracker import calculate_cost

router = APIRouter()

# Default model per provider, used when the client doesn't specify one.
_DEFAULT_MODELS = {
    "openai": "gpt-5.6-terra",
    "claude": "claude-sonnet-5-latest",
}


@router.post("/chat")
async def chat(body: ChatRequest):
    """Stream a chat response from the selected LLM provider.

    1. Resolves the correct adapter via the factory.
    2. Builds the messages list (system prompt + context + user query).
    3. Returns an SSE stream of text deltas.
    4. Sends a final "usage" event with token counts and cost breakdown.
    """
    # Get the adapter for the requested provider.
    try:
        adapter = get_adapter(body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Resolve the model — use the client's override or fall back to the default.
    model = body.model or _DEFAULT_MODELS.get(body.provider, "gpt-5.6-terra")

    # Build the messages list that the adapter will send to the LLM.
    messages: list[dict] = []

    # Add the system prompt if provided.
    if body.system_prompt:
        messages.append({"role": "system", "content": body.system_prompt})

    # If context chunks were provided (from the retrieval phase), inject them
    # into a system-level context block so the LLM can reference them.
    if body.context_chunks:
        # Format each chunk into a readable block for the LLM.
        context_text = "\n\n".join(
            f"[Chunk {c.chunk_id} | score={c.score:.3f}]\n{c.content}"
            for c in body.context_chunks
        )
        messages.append({
            "role": "system",
            "content": f"Use the following context to answer the user's question:\n\n{context_text}",
        })

    # Add the user's actual query as the final message.
    messages.append({"role": "user", "content": body.query})

    # Create an async generator that yields SSE-formatted events.
    async def event_generator():
        # Stream text deltas from the LLM adapter.
        async for token in adapter.stream(messages, model, body.temperature):
            # Each yield sends a "data: <token>\n\n" SSE event to the client.
            yield {"data": token}

        # After streaming completes, calculate and send cost breakdown.
        # The adapter populates self.usage with token counts after the stream.
        if adapter.usage:
            cost = calculate_cost(model, adapter.usage)
            # Send a separate "usage" event so the client can distinguish
            # cost metadata from the text stream.
            yield {"event": "usage", "data": json.dumps(cost.to_dict())}

    # Return the SSE streaming response.
    # media_type ensures the browser/client treats this as an event stream.
    return EventSourceResponse(event_generator())
