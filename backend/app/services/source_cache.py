"""Source cache — keeps the /sources reads out of the vector index.

Both read endpoints join two stores, and the two halves are not equally
expensive.  Object storage answers a whole listing in one paginated call and a
single file in one HEAD.  The vector index does not: finding orphans means
walking every vector id it holds, and describing each file means reading its
metadata back.  That is the half worth caching, and the only half cached here.

Which is also what makes the awkward case tractable.  Files added or removed
directly on the R2 console are picked up on the next request without any cache
involvement at all, because the storage side is never cached — it is read live
every time.  Vectors added or removed directly on the Pinecone console are
caught by a **freshness check** instead: each cached entry records what the
index looked like when it was built, and that record is re-checked, cheaply,
before the entry is used.

Three things can therefore invalidate an entry, in ascending order of cost:

  * **a generation counter**, bumped whenever this application writes — the
    only mechanism that is instant, and the one that covers our own uploads,
    replaces, deletions and indexing runs;
  * **a freshness check** against the index itself — the total vector count for
    a listing, the file's own vector ids for a detail — which is what catches a
    change made outside the application;
  * **a TTL**, the backstop for the one case neither of the above can see: an
    edit made directly on a console that leaves the vector count and the id set
    identical, such as rewriting a chunk's text in place.

A caller that wants none of it passes ``refresh=True``, which reads through and
repopulates.  That is the escape hatch for someone who has just changed
something on a provider console and does not want to wait out the TTL.
"""

import asyncio
import json
import logging
import time
from typing import Any, Optional

from app.config import settings
from app.schemas.source import IndexedDocument, SourceChunk
from app.services import cache, index_catalog
from app.services.provenance import document_id_for
from app.services.vector_store import index_stats

logger = logging.getLogger(__name__)

# Bumped by hand when the cached shape changes, so a deploy cannot read an
# entry written by the previous version into the new models.
NAMESPACE = "sources:v2"

# Generation counter for the whole-index listing. Any write to any file
# changes that listing, so one counter covers it.
EPOCH_KEY = f"{NAMESPACE}:epoch"

# The cached listing: every indexed source key mapped to its index record.
DOCUMENTS_KEY = f"{NAMESPACE}:documents"

# How long the generation counters live. Far longer than the entries they
# guard: a counter that expired and reset to 1 could otherwise match a payload
# built under an earlier generation of the same number.
COUNTER_TTL_SECONDS = 7 * 24 * 60 * 60

# What a vector count of "the index could not tell us" looks like. A fresh
# account has no index yet, and an SDK that stops reporting the field should
# degrade to the TTL rather than to a permanent cache miss.
UNKNOWN_VECTOR_COUNT = -1


def _detail_key(source_key: str) -> str:
    """Cache key for one file's chunks.

    Keyed by document id rather than the object key: object keys carry
    slashes, spaces and unicode, and the derived id is short, stable and safe
    in any cache backend.
    """
    return f"{NAMESPACE}:detail:{document_id_for(source_key)}"


def _version_key(source_key: str) -> str:
    """Generation counter for one file, bumped when that file is written."""
    return f"{NAMESPACE}:version:{document_id_for(source_key)}"


async def _counter(key: str) -> int:
    """Read a generation counter, treating an absent one as generation zero."""
    raw = await cache.get(key)
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0


async def _vector_count() -> int:
    """How many vectors the index currently holds.

    The cheap question that stands in for the expensive one. Walking every
    vector id is what a listing costs; asking the index for its own total is a
    single call, and a total that has not moved means no vector was added or
    removed since the cached listing was built — including by someone working
    directly in the Pinecone console.

    Returns:
        The index's total vector count, or `UNKNOWN_VECTOR_COUNT` when the
        index cannot report one, which falls back to the TTL.
    """
    try:
        stats = await asyncio.to_thread(index_stats)
    except Exception as exc:
        # A probe that fails must not fail the request it was checking, and
        # must not force a rebuild either — the expensive read it guards would
        # be failing for the same reason. It reports "cannot tell", which
        # leaves the generation counter and the TTL as the guards.
        logger.warning("Could not read index stats; falling back to TTL: %s", exc)
        return UNKNOWN_VECTOR_COUNT

    total = stats.get("total_vector_count")
    return int(total) if total is not None else UNKNOWN_VECTOR_COUNT


async def _read(key: str) -> Optional[dict[str, Any]]:
    """Read and parse one cached payload, treating anything unreadable as absent."""
    raw = await cache.get(key)
    if raw is None:
        return None

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None

    return payload if isinstance(payload, dict) else None


async def _write(key: str, payload: dict[str, Any]) -> None:
    """Store one payload under the configured TTL."""
    await cache.set(key, json.dumps(payload), settings.cache_ttl_seconds)


async def load_documents(refresh: bool = False) -> dict[str, IndexedDocument]:
    """Return the index's record of every file it holds vectors for.

    The expensive read behind `GET /sources`, and the one this module exists
    for. A hit costs one call to the index for its vector count; a miss costs
    the full walk.

    Args:
        refresh: Skip the cached entry and rebuild from the index.

    Returns:
        Each indexed source key mapped to the index's record of it.
    """
    if not settings.cache_enabled:
        return await index_catalog.list_indexed_documents()

    # Both are needed either way — to validate a hit, or to stamp a rebuild —
    # and neither depends on the other, so they go together.
    epoch, count = await asyncio.gather(_counter(EPOCH_KEY), _vector_count())

    reason = "refresh requested" if refresh else None

    if reason is None:
        payload = await _read(DOCUMENTS_KEY)
        reason = _listing_miss_reason(payload, epoch, count)

        if reason is None:
            documents = {
                source_key: IndexedDocument.model_validate(document)
                for source_key, document in payload["documents"].items()
            }
            logger.info(
                "sources listing: cache hit (%s), %d indexed document(s)",
                cache.backend_name(),
                len(documents),
            )
            return documents

    started = time.perf_counter()
    documents = await index_catalog.list_indexed_documents()
    elapsed_ms = (time.perf_counter() - started) * 1000

    # The reason is the useful half. A miss on "invalidated by a write" is this
    # application working as intended; a miss on a moved vector count means
    # something changed the index from outside it.
    logger.info(
        "sources listing: cache miss (%s) — %s; walked the index for %d "
        "document(s) in %.0f ms",
        cache.backend_name(),
        reason,
        len(documents),
        elapsed_ms,
    )

    await _write(
        DOCUMENTS_KEY,
        {
            "epoch": epoch,
            "vector_count": count,
            # The verdicts built from this depend on the configured model, so
            # a model change has to read as a miss rather than as fresh data.
            "embedding_model": settings.embedding_model,
            "documents": {
                source_key: document.model_dump(mode="json")
                for source_key, document in documents.items()
            },
        },
    )

    return documents


def _listing_miss_reason(
    payload: Optional[dict[str, Any]], epoch: int, count: int
) -> Optional[str]:
    """Say why a cached listing cannot be used, or None when it can.

    A reason rather than a boolean because the reason is what makes the log
    line worth reading: it distinguishes this application invalidating its own
    cache from the index moving underneath it.

    Args:
        payload: The stored entry, or None when there was nothing to read.
        epoch: The current generation counter.
        count: The index's current total vector count.

    Returns:
        A phrase naming what went stale, or None when nothing did.
    """
    if payload is None:
        return "nothing cached"

    if not isinstance(payload.get("documents"), dict):
        return "cached entry unreadable"

    if payload.get("epoch") != epoch:
        return "invalidated by a write"

    if payload.get("vector_count") != count:
        return f"index vector count moved, {payload.get('vector_count')} -> {count}"

    if payload.get("embedding_model") != settings.embedding_model:
        return (
            f"embedding model changed, {payload.get('embedding_model')!r} -> "
            f"{settings.embedding_model!r}"
        )

    return None


async def load_detail(
    source_key: str, refresh: bool = False
) -> tuple[Optional[IndexedDocument], list[SourceChunk]]:
    """Return one file's index record together with every chunk of it.

    The expensive read behind `GET /sources/{source_key}` is fetching the text
    of every chunk, so that is what is cached. The file's vector ids are listed
    live on each request — one prefix call — and double as the freshness check:
    an id set that has not moved means no chunk was added or removed, whoever
    did it.

    Args:
        source_key: The object key within the bucket.
        refresh: Skip the cached entry and re-read from the index.

    Returns:
        The index's record of the file and its chunks in document order, or
        `(None, [])` when nothing is indexed for it.
    """
    ids = await index_catalog.list_vector_ids_for(source_key)

    if not settings.cache_enabled:
        return await index_catalog.read_document(source_key, ids)

    if not ids:
        # Nothing indexed is not worth an entry, and caching it would make an
        # index that lags a write report the file as empty for a whole TTL.
        return None, []

    key = _detail_key(source_key)
    version = await _counter(_version_key(source_key))

    reason = "refresh requested" if refresh else None

    if reason is None:
        payload = await _read(key)
        reason = _detail_miss_reason(payload, version, ids)

        if reason is None:
            chunks = [
                SourceChunk.model_validate(chunk) for chunk in payload["chunks"]
            ]
            logger.info(
                "sources detail %s: cache hit (%s), %d chunk(s)",
                source_key,
                cache.backend_name(),
                len(chunks),
            )
            return IndexedDocument.model_validate(payload["document"]), chunks

    started = time.perf_counter()
    document, chunks = await index_catalog.read_document(source_key, ids)
    elapsed_ms = (time.perf_counter() - started) * 1000

    logger.info(
        "sources detail %s: cache miss (%s) — %s; fetched %d chunk(s) in %.0f ms",
        source_key,
        cache.backend_name(),
        reason,
        len(chunks),
        elapsed_ms,
    )

    if document is None:
        return None, []

    await _write(
        key,
        {
            "version": version,
            "vector_ids": ids,
            "embedding_model": settings.embedding_model,
            "document": document.model_dump(mode="json"),
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
        },
    )

    return document, chunks


def _detail_miss_reason(
    payload: Optional[dict[str, Any]], version: int, ids: list[str]
) -> Optional[str]:
    """Say why a cached detail cannot be used, or None when it can.

    Args:
        payload: The stored entry, or None when there was nothing to read.
        version: The file's current generation counter.
        ids: The file's vector ids as the index reports them now.

    Returns:
        A phrase naming what went stale, or None when nothing did.
    """
    if payload is None:
        return "nothing cached"

    if not isinstance(payload.get("document"), dict) or not isinstance(
        payload.get("chunks"), list
    ):
        return "cached entry unreadable"

    if payload.get("version") != version:
        return "invalidated by a write"

    cached_ids = payload.get("vector_ids")
    if cached_ids != ids:
        cached_count = len(cached_ids) if isinstance(cached_ids, list) else "?"
        return f"vector ids changed, {cached_count} -> {len(ids)}"

    if payload.get("embedding_model") != settings.embedding_model:
        return (
            f"embedding model changed, {payload.get('embedding_model')!r} -> "
            f"{settings.embedding_model!r}"
        )

    return None


async def invalidate(source_key: str) -> None:
    """Mark one file's cached reads as out of date.

    Called by every service that writes — uploading, replacing, deleting and
    indexing — because the freshness checks are cheap but not instant: the
    index's own statistics lag a write by seconds, so a listing rebuilt on them
    alone could still describe the state before the write that just happened.

    Bumping a counter rather than deleting entries is what keeps this to two
    round trips regardless of how many cached entries the write affects.

    Args:
        source_key: The object key that was written.
    """
    if not settings.cache_enabled:
        return

    await asyncio.gather(
        cache.incr(EPOCH_KEY, COUNTER_TTL_SECONDS),
        cache.incr(_version_key(source_key), COUNTER_TTL_SECONDS),
    )


async def invalidate_all() -> None:
    """Mark every cached listing as out of date.

    For a change that is not about one file — a run finishing, or an operator
    who has just rearranged things on a provider console.
    """
    if not settings.cache_enabled:
        return

    await cache.incr(EPOCH_KEY, COUNTER_TTL_SECONDS)
