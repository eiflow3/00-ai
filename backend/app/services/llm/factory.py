"""Factory function to get the correct LLM adapter based on provider name."""

from app.services.llm.base import BaseLLMAdapter
from app.services.llm.openai_adapter import OpenAIAdapter
from app.services.llm.claude_adapter import ClaudeAdapter

# Registry that maps provider names to their adapter classes.
# Add new providers here as they become available.
_ADAPTERS: dict[str, type[BaseLLMAdapter]] = {
    "openai": OpenAIAdapter,
    "claude": ClaudeAdapter,
}


def get_adapter(provider: str) -> BaseLLMAdapter:
    """Return an instantiated adapter for the given provider name.

    Args:
        provider: The LLM provider key (e.g. "openai", "claude").

    Returns:
        An instance of the matching BaseLLMAdapter subclass.

    Raises:
        ValueError: If the provider is not in the registry.
    """
    # Look up the adapter class from the registry.
    adapter_cls = _ADAPTERS.get(provider)
    if adapter_cls is None:
        # List supported providers in the error for easy debugging.
        supported = ", ".join(_ADAPTERS.keys())
        raise ValueError(
            f"Unsupported LLM provider: '{provider}'. Supported: {supported}"
        )
    # Instantiate and return the adapter.
    return adapter_cls()
