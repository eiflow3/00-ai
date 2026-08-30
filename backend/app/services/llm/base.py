"""Abstract base class that every LLM adapter must implement."""

from abc import ABC, abstractmethod
from typing import AsyncGenerator


class BaseLLMAdapter(ABC):
    """Contract for LLM provider adapters.

    Each adapter must implement the `stream` method which yields text
    deltas as they arrive from the provider's streaming API.
    """

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str, None]:
        """Stream text deltas from the LLM.

        Args:
            messages: A list of message dicts with "role" and "content" keys.
            model: The model name to use (e.g. "gpt-4o-mini", "claude-sonnet-4-20250514").
            temperature: Sampling temperature (0 = deterministic, higher = creative).

        Yields:
            Individual text chunks/deltas as they arrive from the LLM.
        """
        ...  # pragma: no cover
