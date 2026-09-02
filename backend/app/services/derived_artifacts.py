"""Derived artifacts — the stored results of one file's expensive extraction.

A structured source (a PDF) is normalised once, at index time, into canonical
markdown plus a table per artifact.  Those results live in object storage
*beside* the original, under the reserved ``derived/{document_id}/`` prefix
(spelled in app.services.provenance), and every later read — preview, golden
drafting, variant scoring — comes here instead of re-running the extractor.

The artifact is a snapshot of one version of the file: it records the source's
etag, and a mismatch means the file changed underneath it.  A stale artifact is
reported as *absent* rather than served, because stale text handed to a preview
or a golden set is worse than a request to re-index.

Everything here goes through the object_store module by attribute, so the test
suite's vendor fakes cover it without a bindings list.
"""

import asyncio
import logging
from typing import Optional

from app.schemas.extraction import ExtractedTable, ExtractionResult
from app.schemas.source import SourceObject
from app.services import object_store, provenance, text_extraction

logger = logging.getLogger(__name__)

MARKDOWN_CONTENT_TYPE = "text/markdown; charset=utf-8"
EXTRACTION_CONTENT_TYPE = "application/json"


class DerivedTextMissing(LookupError):
    """Raised when a file's text lives in a derived artifact that is not there.

    Either the file was never indexed, or it changed since it was.  Both mean
    the same thing to the caller: index the file first, then its text exists.
    Carries a message written for the person who made the request.
    """

    def __init__(self, source_key: str) -> None:
        self.source_key = source_key
        super().__init__(
            f"{source_key!r} has no extracted text yet. "
            f"Index it first — its text is extracted during indexing."
        )


async def save(source: SourceObject, result: ExtractionResult) -> None:
    """Persist one file's extraction: the markdown, the record, every table.

    Stamps the source's etag into the stored record, which is what later lets
    a read tell this snapshot still describes the live bytes.  Table artifacts
    from an earlier version that no longer exist are pruned, so the store never
    accumulates tables the document stopped having.

    Args:
        source: The source object this extraction was produced from.
        result: The extraction to persist.
    """
    stamped = result.model_copy(update={"source_etag": source.etag})
    document_id = provenance.document_id_for(source.key)

    await object_store.put_object(
        provenance.derived_markdown_key_for(source.key),
        stamped.text.encode("utf-8"),
        MARKDOWN_CONTENT_TYPE,
    )
    await object_store.put_object(
        provenance.derived_extraction_key_for(source.key),
        stamped.model_dump_json().encode("utf-8"),
        EXTRACTION_CONTENT_TYPE,
    )

    wanted = set()
    for table in stamped.tables:
        key = provenance.table_artifact_key_for(document_id, table.table_id)
        wanted.add(key)
        await object_store.put_object(
            key, table.markdown.encode("utf-8"), MARKDOWN_CONTENT_TYPE
        )

    # Tables the document no longer produces — a re-extraction after an edit —
    # are removed, or a listing would offer tables that no chunk links to.
    for stored in await object_store.list_objects(provenance.table_prefix_for(document_id)):
        if stored.key not in wanted:
            await object_store.delete_object(stored.key)

    logger.info(
        "%s: stored derived markdown (%d chars) and %d table artifact(s)",
        source.key,
        len(stamped.text),
        len(stamped.tables),
    )


async def load_extraction(source_key: str) -> Optional[ExtractionResult]:
    """Read one file's stored extraction, if it still describes the live file.

    Args:
        source_key: The object key of the *source* file.

    Returns:
        The stored extraction, or None when there is none — or when the source
        changed since it was written, which amounts to the same thing.
    """
    try:
        raw = await object_store.get_object(
            provenance.derived_extraction_key_for(source_key)
        )
    except FileNotFoundError:
        return None

    result = ExtractionResult.model_validate_json(raw)

    try:
        source = await object_store.head_object(source_key)
    except FileNotFoundError:
        # The source is gone; whatever was extracted describes nothing.
        return None

    if result.source_etag != source.etag:
        logger.info("%s: derived extraction is stale; treating as absent", source_key)
        return None

    return result


async def load_markdown(source_key: str) -> str:
    """Read one file's canonical extracted markdown.

    Args:
        source_key: The object key of the *source* file.

    Returns:
        The stored markdown, page markers and all.

    Raises:
        DerivedTextMissing: When no current extraction exists for the file.
    """
    result = await load_extraction(source_key)
    if result is None:
        raise DerivedTextMissing(source_key)
    return result.text


async def load_source_text(source_key: str) -> str:
    """Read the canonical text of any supported source file.

    The one read-back path every consumer shares: a plain-text file is decoded
    on the spot (off the event loop), while a structured file's text comes from
    its stored artifact — never from re-running the extractor, whose cost and
    configuration belong to indexing.

    Args:
        source_key: The object key within the bucket.

    Returns:
        The file's canonical text.

    Raises:
        FileNotFoundError: When no object exists at that key.
        DerivedTextMissing: When a structured file has not been indexed yet.
        UnsupportedSourceType: If no extractor handles this file type.
    """
    if text_extraction.requires_derived_artifact(source_key):
        return await load_markdown(source_key)

    data = await object_store.get_object(source_key)
    return await asyncio.to_thread(text_extraction.extract_text, source_key, data)


async def list_tables(document_id: str) -> list[ExtractedTable]:
    """List every table artifact a document currently has, in document order.

    Args:
        document_id: The document id from `provenance.document_id_for`.

    Returns:
        The tables as recorded at extraction time; empty when the document has
        none, or was never extracted.
    """
    result = await _extraction_for(document_id)
    return list(result.tables) if result else []


async def get_table(document_id: str, table_id: str) -> Optional[ExtractedTable]:
    """Read one table artifact, with its page and caption.

    Args:
        document_id: The document id from `provenance.document_id_for`.
        table_id: The table's id from `provenance.table_id_for`.

    Returns:
        The table, or None when the document holds no such table.
    """
    result = await _extraction_for(document_id)
    if result is None:
        return None

    for table in result.tables:
        if table.table_id == table_id:
            return table
    return None


async def delete_for(source_key: str) -> int:
    """Remove every derived artifact belonging to one source file.

    Args:
        source_key: The object key of the *source* file.

    Returns:
        How many derived objects were deleted.
    """
    deleted = await object_store.delete_prefix(provenance.derived_prefix_for(source_key))
    if deleted:
        logger.info("%s: removed %d derived artifact(s)", source_key, deleted)
    return deleted


async def _extraction_for(document_id: str) -> Optional[ExtractionResult]:
    """Read a stored extraction record by document id, fresh or not.

    Table reads are keyed by document id because that is all a table link
    carries.  Staleness is not checked here: a link inside an already-retrieved
    chunk should resolve to the table that chunk was embedded with.
    """
    try:
        raw = await object_store.get_object(
            provenance.extraction_key_for_document(document_id)
        )
    except FileNotFoundError:
        return None
    return ExtractionResult.model_validate_json(raw)
