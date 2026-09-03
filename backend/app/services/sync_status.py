"""Sync status — compares what is in storage against what is in the index.

This is the module that answers the question the whole data embedding pipeline
exists for: *this file changed in storage, but did its embeddings change too?*

It joins two independent listings on the source key — the object store's view
of what exists, and the vector index's view of what was embedded — and reduces
each pair to a single verdict a client can render without reimplementing any
of the comparison rules.

There is more than one index side now.  A file can be cut several ways at once,
each cut living in its own namespace, so every row carries a verdict *per
space* as well as one headline verdict.  The headline is measured against
whichever space production currently answers from — anything else would report
a file as unindexed while every answer on the chat screen was grounded in it.
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
    SourceVariant,
)
from app.services import (
    answer_space,
    chunk_variants,
    index_catalog,
    index_registry,
    source_cache,
)
from app.services.object_store import head_object, list_objects
from app.services.provenance import DERIVED_PREFIX
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

    # A run that stopped partway outranks everything below, because those rules
    # all assume the index holds a *complete* document.  It does not: the count
    # recorded at embed time and the number of vectors actually present
    # disagree, which means either a write stopped early or a prune never ran.
    # Left unreported, the file would claim to be current — its first chunk
    # carries the right fingerprint — while retrieval serves text from a
    # version that no longer exists.  A total of zero predates this record and
    # means "cannot tell", so it is not treated as a disagreement.
    if indexed.chunk_total and indexed.chunk_count != indexed.chunk_total:
        return (
            IndexState.INTERRUPTED,
            f"A previous run left {indexed.chunk_count} of "
            f"{indexed.chunk_total} chunks indexed; re-index to complete it.",
        )

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
    variants: Optional[list[SourceVariant]] = None,
) -> SourceStatus:
    """Assemble one file's status row from both sides of the pipeline.

    Args:
        source: The object as it exists in storage, if it still does.
        indexed: What the answering space holds for it, if anything.
        embedding_model: The model currently configured.
        source_key: Key to report when the object is gone; ignored otherwise.
        variants: Every space holding a copy of this file, if they have been
            read. Omitted where a caller only needs the headline verdict.

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
        # What is stored says `state`; what is happening says these. A file can
        # read `not_indexed` while a run is midway through embedding it, and
        # waiting in the queue is not the same as being worked on.
        indexing=index_registry.is_indexing(key),
        queued=index_registry.is_queued(key),
        variants=variants or [],
    )


async def get_status(source_key: str) -> SourceStatus:
    """Return one file's status.

    Args:
        source_key: The object key within the bucket.

    Returns:
        The joined status for that file.
    """
    space = chunk_variants.space_for(await answer_space.current())

    # Both lookups are independent network calls, so run them together.
    source_task = asyncio.create_task(_head_or_none(source_key))
    indexed_task = asyncio.create_task(
        index_catalog.get_indexed_document(source_key, space)
    )

    source, indexed = await asyncio.gather(source_task, indexed_task)

    return build_status(source, indexed, settings.embedding_model, source_key)


async def _head_or_none(source_key: str) -> Optional[SourceObject]:
    """Fetch an object's metadata, or None when it no longer exists."""
    try:
        return await head_object(source_key)
    except FileNotFoundError:
        return None


async def get_detail(source_key: str, refresh: bool = False) -> SourceDetail:
    """Return one file's status together with every chunk indexed from it.

    The storage side is read live and the index side comes through the cache,
    which is also what collapses this to one listing of the file's vector ids:
    the status and the chunks are built from the same read rather than from
    two.

    Args:
        source_key: The object key within the bucket.
        refresh: Re-read the index rather than using what is cached.

    Returns:
        The file's status and its indexed chunks, in document order.
    """
    active = await answer_space.current()
    space = chunk_variants.space_for(active)

    source, (indexed, chunks), copies = await asyncio.gather(
        _head_or_none(source_key),
        source_cache.load_detail(source_key, space, refresh=refresh),
        source_cache.load_copies(refresh=refresh),
    )

    status = build_status(
        source,
        indexed,
        settings.embedding_model,
        source_key,
        variants=_copies_of(source_key, source, copies, active),
    )

    return SourceDetail(status=status, chunks=chunks)


async def list_statuses(
    prefix: str = "",
    refresh: bool = False,
    variant: Optional[str] = None,
) -> list[SourceStatus]:
    """List every source file, joined with its embeddings.

    Both sides are enumerated rather than just storage, so a file whose
    vectors outlived it still appears — as an orphan — instead of vanishing
    from the listing entirely.

    Storage is listed live on every call; the index side comes through the
    cache, which is where the cost is. So a file added or removed directly in
    the bucket shows up immediately, with no cache involvement at all.

    Args:
        prefix: Restrict the listing to keys beginning with this prefix.
        refresh: Re-read the index rather than using what is cached.
        variant: Judge each file against this space instead of the one
            production answers from. What an indexing run passes, so a run
            aimed at one variant is not told a file is up to date on the
            strength of another variant's copy.

    Returns:
        One status per file, newest change first, orphans last.
    """
    target = await answer_space.current() if variant is None else variant

    objects, copies = await asyncio.gather(
        list_objects(prefix),
        source_cache.load_copies(refresh=refresh),
    )

    documents = copies.get(target, {})

    # Derived artifacts share the bucket but are the pipeline's own plumbing —
    # extraction results, not sources. Listing them would offer to index them.
    objects = [
        source for source in objects if not source.key.startswith(DERIVED_PREFIX)
    ]

    stored_keys = {source.key for source in objects}

    # Keys some space holds but storage no longer has — the orphans. Taken
    # across every space rather than the answering one: a file deleted from the
    # bucket while a variant still holds its chunks is exactly as orphaned as
    # one production still holds, and dropping it from the listing would leave
    # the only way to notice on a provider console.
    held_keys = {key for held in copies.values() for key in held}
    orphan_keys = sorted(
        key
        for key in held_keys - stored_keys
        if not prefix or key.startswith(prefix)
    )

    # Storage side first, preserving the newest-change-first order the store
    # returned; orphans trail behind since they have no place in that order.
    statuses = [
        build_status(
            source,
            documents.get(source.key),
            settings.embedding_model,
            variants=_copies_of(source.key, source, copies, target),
        )
        for source in objects
    ]
    statuses.extend(
        build_status(
            None,
            documents.get(key),
            settings.embedding_model,
            key,
            variants=_copies_of(key, None, copies, target),
        )
        for key in orphan_keys
    )

    return statuses


def _copies_of(
    source_key: str,
    source: Optional[SourceObject],
    copies: dict[str, dict[str, IndexedDocument]],
    active: str,
) -> list[SourceVariant]:
    """Describe every space holding a copy of one file.

    Each copy is judged on its own terms, by the same rules that decide the
    headline verdict — so a namespace embedded before the file changed reads as
    stale while one re-cut afterwards reads as current. A single verdict shared
    across all of them would invite scoring a strategy on text that no longer
    exists.

    Args:
        source_key: The file in question.
        source: The object as storage has it, or None when it is gone.
        copies: Every space's records, keyed by variant id.
        active: The variant production answers from.

    Returns:
        One entry per space holding the file, newest copy first.
    """
    held = [
        (variant, documents[source_key])
        for variant, documents in copies.items()
        if source_key in documents
    ]

    described = [
        SourceVariant(
            variant_id=variant,
            label=answer_space.label_for(variant),
            state=_compare(source, document, settings.embedding_model)[0],
            chunk_count=document.chunk_count,
            embedded_at=document.embedded_at,
            active=variant == active,
        )
        for variant, document in held
    ]

    described.sort(
        key=lambda copy: (copy.embedded_at is None, copy.embedded_at), reverse=True
    )
    return described


async def list_reindexable(
    prefix: str = "",
    only_stale: bool = True,
    variant: str = chunk_variants.PRODUCTION_VARIANT,
) -> list[SourceStatus]:
    """List the files an indexing run should process.

    Args:
        prefix: Restrict to keys beginning with this prefix.
        only_stale: Skip files whose embeddings are already up to date.
        variant: The space the run will write to. Staleness is judged against
            that space and no other — a run aimed at one variant must not skip
            a file because a different variant already holds a current copy.

    Returns:
        The files to index, skipping orphans and unreadable file types, which
        no amount of re-indexing would resolve.
    """
    # Deliberately bypasses the cache. This decides what an indexing run will
    # spend money embedding, and it runs once per run rather than once per page
    # load — so it can afford the fresh read, and should not risk paying to
    # re-embed a file on the strength of a cached verdict.
    statuses = await list_statuses(prefix, refresh=True, variant=variant)

    return [
        status
        for status in statuses
        if status.state not in {IndexState.ORPHANED, IndexState.UNSUPPORTED}
        and (status.needs_reindex or not only_stale)
    ]
