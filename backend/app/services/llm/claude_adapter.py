"""Claude adapter — streams messages via the Anthropic SDK."""

from typing import AsyncGenerator

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.llm.base import BaseLLMAdapter

# Upper bound on a single response. Anthropic requires this to be set; it caps
# the answer rather than reserving anything, so a generous value is free.
MAX_OUTPUT_TOKENS = 4096

# Header naming the workspace an identity-linked API key acts in.
WORKSPACE_HEADER = "anthropic-workspace-id"


class ClaudeAdapter(BaseLLMAdapter):
    """Adapter that streams responses from Anthropic's messages API.

    Uses the official AsyncAnthropic client for non-blocking streaming
    inside FastAPI's async event loop.
    """

    def __init__(self):
        super().__init__()
        # Create the async client once per adapter instance.
        # The API key is pulled from our central config.
        #
        # An identity-linked key — one issued to a person rather than to a
        # workspace — is rejected outright unless the request names the
        # workspace it acts in. Sent as a default header so every call carries
        # it; omitted entirely when unset, since an organisation key rejects
        # the header as unexpected.
        headers = (
            {WORKSPACE_HEADER: settings.anthropic_workspace_id}
            if settings.anthropic_workspace_id
            else None
        )
        self.client = AsyncAnthropic(
            api_key=settings.anthropic_api_key, default_headers=headers
        )

    async def stream(
        self,
        messages: list[dict],
        model: str = "claude-sonnet-5",
        temperature: float = 1.0,
    ) -> AsyncGenerator[str, None]:
        """Stream text deltas from Anthropic's messages endpoint.

        Anthropic's messages API uses a different structure than OpenAI:
        - The system prompt must be passed as a top-level `system` param,
          not as a message with role "system".
        - Streamed events have types like "content_block_delta" that carry
          the incremental text.
        - `temperature` is not a parameter of this API. Anthropic replaced it
          with `output_config`, so the argument is accepted to satisfy the
          adapter contract and then ignored rather than passed through, which
          the SDK rejects outright.
        After streaming, populates self.usage with token counts.
        """
        # Anthropic requires the system prompt to be separate from the
        # messages list, so we extract it if present. There can be more than
        # one system message (e.g. the caller's prompt plus a retrieved
        # context block), so collect them all rather than keeping the last.
        system_parts: list[str] = []
        filtered_messages = []
        for msg in messages:
            if msg["role"] == "system":
                # Pull the system prompt out of the messages list.
                system_parts.append(msg["content"])
            else:
                filtered_messages.append(msg)

        # Join multiple system blocks in the order they were supplied.
        system_prompt = "\n\n".join(system_parts)

        # Build the keyword arguments for the API call. Note the absence of
        # temperature — see the docstring.
        kwargs = {
            "model": model,
            "messages": filtered_messages,
            "max_tokens": MAX_OUTPUT_TOKENS,
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

            # After the stream finishes, get the final message to extract
            # token usage. Anthropic natively reports cache tokens.
            final_message = await stream.get_final_message()
            self.usage = {
                "input_tokens": final_message.usage.input_tokens or 0,
                "output_tokens": final_message.usage.output_tokens or 0,
                # Anthropic includes cache_read_input_tokens in its usage.
                "cache_read_tokens": getattr(
                    final_message.usage, "cache_read_input_tokens", 0
                ) or 0,
                # Anthropic includes cache_creation_input_tokens for writes.
                "cache_write_tokens": getattr(
                    final_message.usage, "cache_creation_input_tokens", 0
                ) or 0,
            }
