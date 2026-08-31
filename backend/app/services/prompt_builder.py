"""Prompt construction for the Generation phase.

Turns a query plus its retrieved chunks into the provider-neutral message list
that LLM adapters consume.  Lives here rather than in the router so the prompt's
shape is defined in one place, is unit-testable without HTTP, and is reusable by
any future caller (batch jobs, evals, a non-streaming endpoint).

The *wording* is no longer defined here.  Every template comes from
`prompt_catalog`, and what is actually in force comes from `prompt_store`, so a
change to how answers are grounded is an edit someone makes and a record of who
made it — not a constant in this file and a redeploy.

This function stays pure regardless: it takes the templates it renders rather
than fetching them, so it can still be called with no database behind it.
"""

from typing import Mapping, Optional, Sequence

from app.schemas.prompt import PromptId
from app.schemas.retrieval import RetrievedChunk
from app.services import prompt_catalog
from app.services.prompt_catalog import CHUNK_SEPARATOR, SCORE_PRECISION


def chunk_values(chunk: RetrievedChunk, rank: int) -> dict[str, str]:
    """Render one chunk's fields as the strings a template interpolates.

    Everything is a string, including the score: a template writes a bare
    `{score}` and gets a rounded figure, rather than having to carry a format
    spec that would break the moment the field's type changed.

    Args:
        chunk: The retrieved chunk.
        rank: Its position in the ranking, starting at 1 — the numbering a
            person reads, not the index a list uses.

    Returns:
        Values keyed by the placeholder names the chunk format may use.
    """
    return {
        "content": chunk.content,
        "chunk_id": chunk.chunk_id,
        "score": f"{chunk.score:.{SCORE_PRECISION}f}",
        "source": chunk.source or "",
        "document_id": chunk.document_id or "",
        "rank": str(rank),
    }


def format_context(
    chunks: Sequence[RetrievedChunk],
    chunk_template: Optional[str] = None,
) -> str:
    """Render retrieved chunks into a single readable context block.

    Each chunk is rendered through the chunk format, which decides what the
    model can see about it — and so what it can cite back.

    Args:
        chunks: The chunks to render, best match first.
        chunk_template: Override for the chunk format; defaults to the shipped
            one so a caller without a prompt set still gets sensible output.

    Returns:
        The chunks joined into one block.
    """
    template = (
        chunk_template
        if chunk_template is not None
        else prompt_catalog.defaults()[PromptId.CHUNK_FORMAT]
    )
    return CHUNK_SEPARATOR.join(
        prompt_catalog.render(template, chunk_values(chunk, rank))
        for rank, chunk in enumerate(chunks, start=1)
    )


def build_messages(
    query: str,
    chunks: Sequence[RetrievedChunk] = (),
    system_prompt: Optional[str] = None,
    grounded: bool = True,
    prompts: Optional[Mapping[PromptId, str]] = None,
) -> list[dict]:
    """Assemble the message list sent to the LLM.

    Order matters: the system prompt first, then the retrieved context block,
    then the user's question last so it sits closest to the generation.

    Args:
        query: The user's question.
        chunks: Retrieved context chunks; omit for an ungrounded answer.
        system_prompt: Instruction steering the model's behaviour. `None` falls
            back to the system prompt in force; an empty string suppresses it,
            which is how a caller asks for no system message at all.
        grounded: Whether retrieval was meant to run. False means the caller
            asked for an ungrounded answer, so the empty-retrieval fallback is
            not used — nothing failed, there was simply nothing to search.
        prompts: The templates in force. Defaults to the shipped ones so this
            stays callable from a test with no store behind it.

    Returns:
        A list of {"role", "content"} dicts in provider-neutral form.
    """
    templates = dict(prompt_catalog.defaults())
    if prompts:
        templates.update(prompts)

    messages: list[dict] = []

    # The caller's own system prompt wins; None means "whatever is configured".
    effective_system = (
        system_prompt if system_prompt is not None else templates[PromptId.SYSTEM]
    )
    if effective_system.strip():
        messages.append({"role": "system", "content": effective_system})

    if chunks:
        # Inject the retrieved chunks as a separate system-level context block.
        block = prompt_catalog.render(
            templates[PromptId.CONTEXT_BLOCK],
            {"chunks": format_context(chunks, templates[PromptId.CHUNK_FORMAT])},
        )
        messages.append({"role": "system", "content": block})
    elif grounded and templates[PromptId.NO_CONTEXT].strip():
        # Retrieval ran and found nothing. Saying so beats letting the model
        # answer from general knowledge and read as though it were grounded.
        messages.append({
            "role": "system",
            "content": prompt_catalog.render(
                templates[PromptId.NO_CONTEXT], {"query": query}
            ),
        })

    # Add the user's actual query as the final message.
    messages.append({"role": "user", "content": query})

    return messages


def resolve_system_prompt(
    system_prompt: Optional[str],
    prompts: Optional[Mapping[PromptId, str]] = None,
) -> str:
    """Return the system prompt a request will actually run under.

    The trace records what the model was shown, not what the client sent — and
    with a configured default those are no longer the same thing.
    """
    templates = dict(prompt_catalog.defaults())
    if prompts:
        templates.update(prompts)

    effective = system_prompt if system_prompt is not None else templates[PromptId.SYSTEM]
    return effective if effective.strip() else ""


def sample_chunks(count: int) -> list[RetrievedChunk]:
    """Build stand-in chunks for previewing the assembled prompt.

    Scores step downward so a preview shows a plausible ranking rather than a
    column of identical numbers.
    """
    return [
        RetrievedChunk(
            chunk_id=f"doc-1#{index}",
            document_id="doc-1",
            source="reports/meridian-fy2025.txt",
            score=max(0.0, 0.812 - index * 0.037),
            content=(
                f"Sample passage {index + 1} — the text of a retrieved chunk "
                "appears here, exactly as it was indexed."
            ),
        )
        for index in range(count)
    ]
