"""Sync status — compares what is in storage against what is in the index.

This is the module that answers the question the whole data embedding pipeline
exists for: *this file changed in storage, but did its embeddings change too?*

It joins two independent listings on the source key — the object store's view
of what exists, and the vector index's view of what was embedded — and reduces
each pair to a single verdict a client can render without reimplementing any
of the comparison rules.
"""

import asyncio
from typing import Optional

from app.config import settings
from app.schemas.source import (
    IndexedDocument,
    IndexState,
    SourceDetail,
    SourceObject,
    SourceStatus,
)
from app.services import index_catalog, index_registry
from app.services.object_store import head_object, list_objects
from app.services.text_extraction import is_supported


def _compare(
    source: Optional[SourceObject],
    indexed: Optional[IndexedDocument],
    embedding_model: str,
) -> tuple[IndexState, str]:
    """Decide one file's state from its storage and index records.

    Rules are evaluated in precedence order, first match winning, so a file
    that is both stale and built by an old model reports the reason that
    matters most.

    Args:
        source: The object as it exists in storage, if it still does.
        indexed: What the index holds for it, if anything.
        embedding_model: The model currently configured.

    Returns:
        The state, and a plain-language reason for it.
    """
    # Vectors with no file behind them. Re-indexing cannot fix this; the
    # resolution is to delete the vectors, so it is called out separately.
    if source is None:
        return (
            IndexState.ORPHANED,
            "Vectors exist for a file that is no longer in object storage.",
        )

    # Nothing this pipeline can read, so it will never be indexed. Reported
    # rather than hidden, so an upload in the wrong format is visible.
    if not is_supported(source.key):
        return (
            IndexState.UNSUPPORTED,
            "No extractor for this file type; it will be skipped during indexing.",
        )

    if indexed is None:
        return IndexState.NOT_INDEXED, "This file has never been embedded."

    # The content check comes first because it is the common case and the one
    # the user asked to see.  The etag decides it, not the timestamp: object
    # storage bumps last-modified on any rewrite, including one that stores
    # byte-identical content, so a timestamp comparison would report every
    # re-upload as stale.  The timestamps are still surfaced — they are what a
    # person reads — but they do not cast the vote.
    if indexed.source_etag and source.etag and indexed.source_etag != source.etag:
        return (
            IndexState.STALE_CONTENT,
            "The file changed in object storage after it was embedded.",
        )

    # Vectors from another model cannot be scored against queries embedded
    # with the current one, so this is as urgent as changed content.
    if indexed.embedding_model and indexed.embedding_model != embedding_model:
        return (
            IndexState.STALE_MODEL,
            f"Embedded with {indexed.embedding_model!r}, but "
            f"{embedding_model!r} is configured now.",
        )

    return IndexState.CURRENT, "Embeddings match the file in object storage."


def build_status(
    source: Optional[SourceObject],
    indexed: Optional[IndexedDocument],
    embedding_model: str,
    source_key: str = "",
) -> SourceStatus:
    """Assemble one file's status row from both sides of the pipeline.

    Args:
        source: The object as it exists in storage, if it still does.
        indexed: What the index holds for it, if anything.
        embedding_model: The model currently configured.
        source_key: Key to report when the object is gone; ignored otherwise.

    Returns:
        The joined status, carrying both records and the verdict.
    """
    state, detail = _compare(source, indexed, embedding_model)

    key = source.key if source else (indexed.source_key if indexed else source_key)

    return SourceStatus(
        source_key=key,
        state=state,
        source=source,
        indexed=indexed,
        detail=detail,
        # What is stored says `state`; what is happening says this. A file can
        # read `not_indexed` while a run is midway through embedding it.
        indexing=index_registry.is_indexing(key),
    )


async def get_status(source_key: str) -> SourceStatus:
    """Return one file's status.

    Args:
        source_key: The object key within the bucket.

    Returns:
        The joined status for that file.
    """
    # Both lookups are independent network calls, so run them together.
    source_task = asyncio.create_task(_head_or_none(source_key))
    indexed_task = asyncio.create_task(index_catalog.get_indexed_document(source_key))

    source, indexed = await asyncio.gather(source_task, indexed_task)

    return build_status(source, indexed, settings.embedding_model, source_key)


async def _head_or_none(source_key: str) -> Optional[SourceObject]:
    """Fetch an object's metadata, or None when it no longer exists."""
    try:
        return await head_object(source_key)
    except FileNotFoundError:
        return None


async def get_detail(source_key: str) -> SourceDetail:
    """Return one file's status together with every chunk indexed from it.

    Args:
        source_key: The object key within the bucket.

    Returns:
        The file's status and its indexed chunks, in document order.
    """
    status, chunks = await asyncio.gather(
        get_status(source_key),
        index_catalog.get_chunks(source_key),
    )
    return SourceDetail(status=status, chunks=chunks)


async def list_statuses(prefix: str = "") -> list[SourceStatus]:
    """List every source file, joined with its embeddings.

    Both sides are enumerated rather than just storage, so a file whose
    vectors outlived it still appears — as an orphan — instead of vanishing
    from the listing entirely.

    Args:
        prefix: Restrict the listing to keys beginning with this prefix.

    Returns:
        One status per file, newest change first, orphans last.
    """
    objects, indexed_keys = await asyncio.gather(
        list_objects(prefix),
        index_catalog.list_indexed_source_keys(),
    )

    stored_keys = {source.key for source in objects}

    # Keys the index holds but storage no longer has — the orphans.
    orphan_keys = sorted(
        key
        for key in indexed_keys - stored_keys
        if not prefix or key.startswith(prefix)
    )

    # Only files the index actually knows about need a provenance lookup, and
    # those lookups are independent — run them together rather than walking
    # the bucket one round trip at a time.
    lookup_keys = [key for key in stored_keys if key in indexed_keys] + orphan_keys
    documents = dict(
        zip(
            lookup_keys,
            await asyncio.gather(
                *(index_catalog.get_indexed_document(key) for key in lookup_keys)
            ),
        )
    )

    # Storage side first, preserving the newest-change-first order the store
    # returned; orphans trail behind since they have no place in that order.
    statuses = [
        build_status(source, documents.get(source.key), settings.embedding_model)
        for source in objects
    ]
    statuses.extend(
        build_status(None, documents.get(key), settings.embedding_model, key)
        for key in orphan_keys
    )

    return statuses


async def list_reindexable(prefix: str = "", only_stale: bool = True) -> list[SourceStatus]:
    """List the files an indexing run should process.

    Args:
        prefix: Restrict to keys beginning with this prefix.
        only_stale: Skip files whose embeddings are already up to date.

    Returns:
        The files to index, skipping orphans and unreadable file types, which
        no amount of re-indexing would resolve.
    """
    statuses = await list_statuses(prefix)

    return [
        status
        for status in statuses
        if status.state not in {IndexState.ORPHANED, IndexState.UNSUPPORTED}
        and (status.needs_reindex or not only_stale)
    ]
