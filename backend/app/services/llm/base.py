"""Abstract base class that every LLM adapter must implement."""

from abc import ABC, abstractmethod
from typing import AsyncGenerator


class BaseLLMAdapter(ABC):
    """Contract for LLM provider adapters.

    Each adapter must implement the `stream` method which yields text
    deltas as they arrive from the provider's streaming API.

    After streaming completes, the adapter populates `self.usage` with
    token counts so the cost tracker can calculate the request cost.
    """

    def __init__(self):
        # Usage metadata populated after the stream finishes.
        # Keys: input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
        self.usage: dict | None = None

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 1.0,
    ) -> AsyncGenerator[str, None]:
        """Stream text deltas from the LLM.

        Args:
            messages: A list of message dicts with "role" and "content" keys.
            model: The model name to use (e.g. "gpt-5.6-terra", "claude-sonnet-5").
            temperature: Sampling temperature. Defaults to 1.0 because current
                models on both providers reject anything else — OpenAI's reject
                a non-default value outright, and Anthropic's no longer take
                the parameter at all. Adapters may ignore it.

        Yields:
            Individual text chunks/deltas as they arrive from the LLM.
        """
        ...  # pragma: no cover
