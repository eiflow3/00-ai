"""Table describer — swaps each extracted table for prose the index can find.

A table embeds badly: cut into chunks it becomes a soup of numbers whose
similarity to any question is noise.  So before a document is stored and
chunked, each table found in it is replaced by an LLM-written description that
ends in a link back to the stored table artifact.  Retrieval matches the
prose; the link is how a reader gets from an answer back to the real grid.

Ordering matters for cost: a table is *located* in the document text first,
and only a table that can actually be replaced is sent to the model.  A table
that cannot be found (an export-formatting mismatch) stays inline — which
still embeds, just worse — and is reported as a warning, never a failure.
The same is true of a per-table LLM error: the run finishes, the table stays
inline, the warning says which table and why.

The invariant this module maintains: `ExtractionResult.pages` always describes
the final `text`, after every splice.
"""

import logging

from app.config import settings
from app.schemas.extraction import ExtractedTable, ExtractionResult, PageSpan
from app.schemas.prompt import PromptId
from app.services import prompt_catalog, prompt_store, provenance
from app.services.llm import catalog as model_catalog
from app.services.llm.base import BaseLLMAdapter
from app.services.llm.factory import get_adapter

logger = logging.getLogger(__name__)


def _link_label(table: ExtractedTable) -> str:
    """The link text a reader sees where the table used to be."""
    if table.caption:
        return table.caption
    if table.page:
        return f"Table on page {table.page}"
    return table.table_id.replace("-", " ").capitalize()


def _spliced(
    text: str, pages: list[PageSpan], start: int, end: int, replacement: str
) -> tuple[str, list[PageSpan]]:
    """Replace text[start:end] with `replacement`, keeping page spans true.

    A boundary before the splice stays put, one after it shifts by the length
    delta, and one *inside* the replaced region — a table crossing a page
    break — lands at the end of the replacement, so spans stay contiguous.
    """
    delta = len(replacement) - (end - start)
    inside = start + len(replacement)

    def moved(offset: int) -> int:
        if offset <= start:
            return offset
        if offset >= end:
            return offset + delta
        return inside

    return (
        text[:start] + replacement + text[end:],
        [
            PageSpan(
                page=span.page,
                start_offset=moved(span.start_offset),
                end_offset=moved(span.end_offset),
            )
            for span in pages
        ],
    )


async def _describe(
    adapter: BaseLLMAdapter, model: str, template: str, table: ExtractedTable
) -> str:
    """Ask the model for one table's stand-in prose.

    The adapters stream because chat needs them to; here the whole reply is
    wanted at once, so the deltas are accumulated.
    """
    prompt = prompt_catalog.render(
        template,
        {
            "table_markdown": table.markdown,
            "caption": table.caption or "",
            "page": str(table.page) if table.page else "",
        },
    )

    reply = "".join(
        [chunk async for chunk in adapter.stream([{"role": "user", "content": prompt}], model)]
    )
    return reply.strip()


async def describe_tables(
    result: ExtractionResult, source_key: str
) -> tuple[ExtractionResult, list[str]]:
    """Replace each locatable table in the text with described prose plus link.

    Args:
        result: The extraction as the extractor produced it, tables inline.
        source_key: The file being indexed, for link ids and messages.

    Returns:
        The extraction with descriptions spliced in (pages kept true), and a
        warning per table that had to be left inline — none of which failed
        the document.
    """
    if not result.tables:
        return result, []

    document_id = provenance.document_id_for(source_key)
    templates = await prompt_store.active()
    template = templates.get(PromptId.TABLE_DESCRIPTION, "")

    provider = settings.table_description_provider
    model = settings.table_description_model or model_catalog.DEFAULT_MODELS.get(provider, "")

    text = result.text
    pages = list(result.pages)
    warnings: list[str] = []
    adapter: BaseLLMAdapter | None = None

    for table in result.tables:
        # Locate before describing: a table that cannot be replaced would make
        # its description a paid-for orphan.
        start = text.find(table.markdown)
        if start < 0:
            warnings.append(
                f"{table.table_id}: not found in the document text; left as-is."
            )
            continue

        try:
            if adapter is None:
                adapter = get_adapter(provider)
            description = await _describe(adapter, model, template, table)
            if not description:
                raise ValueError("the model returned an empty description")
        except Exception as exc:  # noqa: BLE001 — any per-table failure is non-fatal
            warnings.append(
                f"{table.table_id}: description failed ({exc}); the raw table stays embedded."
            )
            logger.warning("%s: %s could not be described: %s", source_key, table.table_id, exc)
            continue

        link = provenance.table_link_for(document_id, table.table_id, _link_label(table))
        text, pages = _spliced(
            text,
            pages,
            start,
            start + len(table.markdown),
            f"{description}\n\n{link}",
        )
        logger.info("%s: %s described and linked", source_key, table.table_id)

    return result.model_copy(update={"text": text, "pages": pages}), warnings
