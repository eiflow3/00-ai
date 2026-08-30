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
        super().__init__()
        # Create the async client once per adapter instance.
        # The API key is pulled from our central config.
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def stream(
        self,
        messages: list[dict],
        model: str = "gpt-5.6-terra",
        temperature: float = 0.3,
    ) -> AsyncGenerator[str, None]:
        """Stream text deltas from OpenAI's chat completions endpoint.

        Iterates over the streamed response chunks and yields only the
        text content (delta) of each chunk, skipping empty deltas.
        After streaming, populates self.usage with token counts.
        """
        # Call the OpenAI API with stream=True to receive incremental chunks.
        # stream_options={"include_usage": True} tells OpenAI to include
        # token usage info in the final chunk of the stream.
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )

        # Each chunk contains a choices list; the first choice's delta
        # holds the incremental text content.
        async for chunk in response:
            # The final chunk has usage data but empty choices.
            if chunk.usage is not None:
                # Extract token counts from the final chunk's usage object.
                self.usage = {
                    "input_tokens": chunk.usage.prompt_tokens or 0,
                    "output_tokens": chunk.usage.completion_tokens or 0,
                    # OpenAI reports cached tokens under prompt_tokens_details.
                    "cache_read_tokens": getattr(
                        chunk.usage.prompt_tokens_details, "cached_tokens", 0
                    ) or 0,
                    "cache_write_tokens": 0,  # OpenAI doesn't report write tokens separately
                }

            # Only yield when there is actual text content in the delta.
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
