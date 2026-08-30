"""Prompt construction for the Generation phase.

Turns a query plus its retrieved chunks into the provider-neutral message
list that LLM adapters consume.  Lives here rather than in the router so the
prompt's shape is defined in one place, is unit-testable without HTTP, and is
reusable by any future caller (batch jobs, evals, a non-streaming endpoint).
"""

from typing import Optional, Sequence

from app.schemas.retrieval import RetrievedChunk

# Instruction that precedes the retrieved context block.
CONTEXT_INSTRUCTION = "Use the following context to answer the user's question:"


def format_context(chunks: Sequence[RetrievedChunk]) -> str:
    """Render retrieved chunks into a single readable context block.

    Each chunk is labelled with its id and similarity score so the model can
    weigh sources and cite them back by id.
    """
    return "\n\n".join(
        f"[Chunk {chunk.chunk_id} | score={chunk.score:.3f}]\n{chunk.content}"
        for chunk in chunks
    )


def build_messages(
    query: str,
    chunks: Sequence[RetrievedChunk] = (),
    system_prompt: Optional[str] = None,
) -> list[dict]:
    """Assemble the message list sent to the LLM.

    Order matters: the system prompt first, then the retrieved context block,
    then the user's question last so it sits closest to the generation.

    Args:
        query: The user's question.
        chunks: Retrieved context chunks; omit for an ungrounded answer.
        system_prompt: Optional instruction steering the model's behaviour.

    Returns:
        A list of {"role", "content"} dicts in provider-neutral form.
    """
    messages: list[dict] = []

    # Add the system prompt if provided.
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # Inject the retrieved chunks as a separate system-level context block.
    if chunks:
        messages.append({
            "role": "system",
            "content": f"{CONTEXT_INSTRUCTION}\n\n{format_context(chunks)}",
        })

    # Add the user's actual query as the final message.
    messages.append({"role": "user", "content": query})

    return messages
