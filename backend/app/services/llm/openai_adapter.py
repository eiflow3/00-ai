"""OpenAI adapter — streams chat completions via the OpenAI SDK."""

from typing import AsyncGenerator

from openai import AsyncOpenAI

from app.config import settings
from app.services.llm.base import BaseLLMAdapter


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter that streams responses from OpenAI's chat completions API.

    Uses the official AsyncOpenAI client so we get non-blocking I/O
    inside FastAPI's async event loop.
    """

    def __init__(self):
        # Create the async client once per adapter instance.
        # The API key is pulled from our central config.
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def stream(
        self,
        messages: list[dict],
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
    ) -> AsyncGenerator[str, None]:
        """Stream text deltas from OpenAI's chat completions endpoint.

        Iterates over the streamed response chunks and yields only the
        text content (delta) of each chunk, skipping empty deltas.
        """
        # Call the OpenAI API with stream=True to receive incremental chunks.
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )

        # Each chunk contains a choices list; the first choice's delta
        # holds the incremental text content.
        async for chunk in response:
            # Extract the text delta from the chunk.
            delta = chunk.choices[0].delta
            # Only yield when there is actual text content in the delta.
            if delta.content:
                yield delta.content
