"""Claude adapter — streams messages via the Anthropic SDK."""

from typing import AsyncGenerator

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.llm.base import BaseLLMAdapter


class ClaudeAdapter(BaseLLMAdapter):
    """Adapter that streams responses from Anthropic's messages API.

    Uses the official AsyncAnthropic client for non-blocking streaming
    inside FastAPI's async event loop.
    """

    def __init__(self):
        # Create the async client once per adapter instance.
        # The API key is pulled from our central config.
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def stream(
        self,
        messages: list[dict],
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.3,
    ) -> AsyncGenerator[str, None]:
        """Stream text deltas from Anthropic's messages endpoint.

        Anthropic's messages API uses a different structure than OpenAI:
        - The system prompt must be passed as a top-level `system` param,
          not as a message with role "system".
        - Streamed events have types like "content_block_delta" that carry
          the incremental text.
        """
        # Anthropic requires the system prompt to be separate from the
        # messages list, so we extract it if present.
        system_prompt = None
        filtered_messages = []
        for msg in messages:
            if msg["role"] == "system":
                # Pull the system prompt out of the messages list.
                system_prompt = msg["content"]
            else:
                filtered_messages.append(msg)

        # Build the keyword arguments for the API call.
        kwargs = {
            "model": model,
            "messages": filtered_messages,
            "temperature": temperature,
            "max_tokens": 4096,
        }
        # Only include system if we actually have a system prompt.
        if system_prompt:
            kwargs["system"] = system_prompt

        # Use the async streaming context manager to iterate over events.
        async with self.client.messages.stream(**kwargs) as stream:
            # .text_stream yields only the text deltas, which is exactly
            # what we need for our SSE response.
            async for text in stream.text_stream:
                yield text
