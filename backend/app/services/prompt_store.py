"""Which prompt text is in force, and how it gets changed.

Sits on `prompt_catalog` (what ships) and `prompt_db` (what was changed), and
answers the only two questions anything else has: *show me the prompts* for the
editor, and *give me the templates* for the pipeline.

An override is stored only where one exists.  That is what makes a reset a
delete, and what makes a default edit itself when the code changes — a prompt
nobody has touched follows the code, and one somebody has touched does not
silently revert underneath them.

Cached in process, and deliberately *not* in Redis — the opposite decision from
the source reads, for the opposite reason.  The source reads are network calls
to two other services; this is a four-row table on the local disk, and measured
against this deployment's Redis a round trip costs about 1.8x what simply
reading the table does.  Caching it there would make it slower.  Holding it in
memory instead costs nothing at all.

What makes that safe is the writer count.  The source cache has to assume
someone is editing R2 or Pinecone from a console, so it re-checks freshness on
every read.  Nothing edits this table but `save` and `reset` below, so clearing
the memo there is complete — there is no path by which it can go stale, and the
guarantee the feature makes holds exactly: an edit applies to the next question.

That reasoning is single-process.  A second worker would hold its own memo and
never hear about the first one's write, so `prompt_cache_enabled` turns this off
and goes back to reading the table every time — which, at 0.06 ms, is a price
worth paying for correctness the moment there is more than one worker.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.schemas.prompt import (
    Prompt,
    PromptId,
    PromptMessage,
    PromptPreview,
    PromptPreviewRequest,
)
from app.services import prompt_catalog, prompt_db
from app.services.prompt_catalog import InvalidTemplate, UnknownPrompt

logger = logging.getLogger(__name__)

def _overrides_sync() -> dict[str, dict]:
    """Read every saved override, keyed by prompt id."""
    rows = prompt_db.read("SELECT id, template, updated_at FROM prompt_overrides")
    return {row["id"]: dict(row) for row in rows}


# The overrides as last read, or None when the table must be read again.  Rows
# are plain dicts rather than sqlite3.Row objects so nothing held here keeps a
# cursor alive.
_overrides: Optional[dict[str, dict]] = None


async def _current_overrides() -> dict[str, dict]:
    """Return the saved overrides, reading them only when the memo is empty.

    Returns:
        Each overridden prompt id mapped to its stored row.
    """
    global _overrides

    if not settings.prompt_cache_enabled:
        return await asyncio.to_thread(_overrides_sync)

    if _overrides is None:
        _overrides = await asyncio.to_thread(_overrides_sync)
        logger.debug("Prompt overrides read from disk: %d stored", len(_overrides))

    return _overrides


def invalidate() -> None:
    """Forget the memo, so the next read goes back to the table.

    Synchronous and lock-free: it replaces one module-level reference, which
    needs no lock to be correct, and it is called from the write paths where
    awaiting anything extra would only widen the window it exists to close.
    """
    global _overrides

    _overrides = None


def _to_prompt(definition: Prompt, override: Optional[dict]) -> Prompt:
    """Layer a saved override onto a shipped definition.

    Args:
        definition: The registry entry, carrying the default.
        override: The stored row, if this prompt has been edited.

    Returns:
        The prompt as it now stands, still carrying its default so a client can
        show what a reset would restore.
    """
    if override is None:
        return definition.model_copy(update={"template": definition.default_template})

    return definition.model_copy(update={
        "template": override["template"],
        "edited": True,
        "updated_at": datetime.fromtimestamp(override["updated_at"], tz=timezone.utc),
    })


async def list_prompts() -> list[Prompt]:
    """Return every prompt in the order a request assembles them."""
    overrides = await _current_overrides()
    return [
        _to_prompt(definition, overrides.get(definition.id.value))
        for definition in prompt_catalog.PROMPTS
    ]


async def get(prompt_id: str) -> Prompt:
    """Return one prompt as it currently stands.

    Raises:
        UnknownPrompt: If no prompt carries this id.
    """
    definition = prompt_catalog.definition(prompt_id)
    overrides = await _current_overrides()
    return _to_prompt(definition, overrides.get(definition.id.value))


async def save(prompt_id: str, template: str) -> Prompt:
    """Override one prompt's text.

    Saving the shipped default is treated as a reset rather than an override,
    so a prompt is only ever marked edited while it actually differs — and it
    goes back to following the code once it matches it again.

    Args:
        prompt_id: The prompt to change.
        template: The replacement text.

    Returns:
        The prompt as it now stands.

    Raises:
        UnknownPrompt: If no prompt carries this id.
        InvalidTemplate: If the text would not render in the pipeline.
    """
    definition = prompt_catalog.definition(prompt_id)
    validated = prompt_catalog.validate(prompt_id, template)

    if validated == definition.default_template:
        return await reset(prompt_id)

    await asyncio.to_thread(
        prompt_db.write,
        "INSERT INTO prompt_overrides (id, template, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET template = excluded.template, "
        "updated_at = excluded.updated_at",
        (definition.id.value, validated, time.time()),
    )
    invalidate()
    logger.info("Prompt %r overridden (%d chars)", definition.id.value, len(validated))

    return await get(prompt_id)


async def reset(prompt_id: str) -> Prompt:
    """Discard one prompt's override, returning it to the shipped default.

    Resetting a prompt that was never edited is not an error — the caller asked
    for a state the store is already in.

    Raises:
        UnknownPrompt: If no prompt carries this id.
    """
    definition = prompt_catalog.definition(prompt_id)

    removed = await asyncio.to_thread(
        prompt_db.write,
        "DELETE FROM prompt_overrides WHERE id = ?",
        (definition.id.value,),
    )
    invalidate()

    if removed:
        logger.info("Prompt %r reset to its default", definition.id.value)

    return await get(prompt_id)


async def active() -> dict[PromptId, str]:
    """Return the templates the pipeline should render this request with.

    Served from the memo, which the write paths clear — so this still answers
    with the text saved a moment ago, which is the whole reason these stopped
    being constants.
    """
    overrides = await _current_overrides()
    return {
        definition.id: (
            overrides[definition.id.value]["template"]
            if definition.id.value in overrides
            else definition.default_template
        )
        for definition in prompt_catalog.PROMPTS
    }


async def preview(request: PromptPreviewRequest) -> PromptPreview:
    """Render the prompts in force into the messages they would produce.

    The point of the editor is that the assembled request is visible, not
    inferred: a template reads differently on its own than it does repeated
    once per chunk and wrapped in the block that carries it.

    Args:
        request: The question, how many stand-in chunks to render, and whether
            to preview the grounded path or the ungrounded one.

    Returns:
        The message list, in the order the adapters receive it.

    Raises:
        InvalidTemplate: If a saved template no longer renders — possible when
            a variable has been retired from the registry since it was saved.
    """
    # Imported here: prompt_builder reads this module's output, so importing it
    # at module scope would close the loop.
    from app.services import prompt_builder

    chunks = prompt_builder.sample_chunks(request.chunk_count)
    messages = prompt_builder.build_messages(
        query=request.query,
        chunks=chunks,
        grounded=request.grounded,
        prompts=await active(),
    )

    rendered = [PromptMessage(role=m["role"], content=m["content"]) for m in messages]
    return PromptPreview(
        messages=rendered,
        character_count=sum(len(message.content) for message in rendered),
    )


__all__ = [
    "InvalidTemplate",
    "UnknownPrompt",
    "active",
    "get",
    "invalidate",
    "list_prompts",
    "preview",
    "reset",
    "save",
]
