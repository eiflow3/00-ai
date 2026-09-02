"""Index catalog — reads the vector index as a catalog of source documents.

The retrieval service asks the index "what is similar to this query?".  This
module asks it a different question: "which source files do you hold, and what
did they look like when you embedded them?"

That answer is only possible because of the identity scheme in
app.services.provenance — every vector id is prefixed with its document id, so
one file's chunks can be listed, inspected and deleted as a unit without any
separate bookkeeping.
"""

import asyncio
from typing import Any, Optional

from app.schemas.source import IndexedDocument, SourceChunk
from app.services.embeddings import EMBEDDING_MODEL_METADATA_KEY
from app.services.provenance import (
    METADATA_CHUNK_INDEX,
    METADATA_CHUNK_TOTAL,
    METADATA_CONTENT,
    METADATA_EMBEDDED_AT,
    METADATA_SOURCE_ETAG,
    METADATA_SOURCE_KEY,
    METADATA_SOURCE_LAST_MODIFIED,
    document_id_for,
    parse_vector_id,
    to_datetime,
    vector_id_prefix_for,
)
from app.services.vector_store import (
    VectorSpace,
    delete_vectors,
    fetch_vectors,
    list_vector_ids,
)


def _metadata_of(record: dict[str, Any]) -> dict[str, Any]:
    """Pull the metadata dict out of a fetched vector record."""
    metadata = record.get("metadata") or {}
    return dict(metadata) if not isinstance(metadata, dict) else metadata


def _as_int(value: Any) -> int:
    """Read a metadata number as an int, treating anything unreadable as zero.

    Pinecone returns numeric metadata as floats, and vectors written before a
    field existed carry nothing at all.  Zero means "not recorded", which the
    comparison rules treat as "cannot tell" rather than as a real count.
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


async def list_vector_ids_for(
    source_key: str, space: Optional[VectorSpace] = None
) -> list[str]:
    """List every vector id belonging to one source file.

    Args:
        source_key: The object key within the bucket.
        space: Which index and namespace to look in. Defaults to production.

    Returns:
        The file's vector ids, sorted so they run in chunk order.
    """
    prefix = vector_id_prefix_for(source_key)
    ids = await asyncio.to_thread(list_vector_ids, prefix, space)
    # Ids are zero-padded, so a lexical sort is chunk order.
    return sorted(ids)


def _to_indexed_document(
    source_key: str, metadata: dict[str, Any], chunk_count: int
) -> IndexedDocument:
    """Build one file's index record from a chunk's metadata.

    Every chunk of a file carries the same provenance, so any one of them
    describes the whole document — which is why a listing never has to read
    more than one vector per file.

    Args:
        source_key: The key to fall back on when metadata does not carry one.
        metadata: Metadata read off one of the file's vectors.
        chunk_count: How many vectors the file actually occupies.

    Returns:
        The index's record of the file.
    """
    return IndexedDocument(
        # Prefer the stored key over the argument: it is what was actually
        # embedded, so a hash collision or a caller's typo shows up here.
        source_key=str(metadata.get(METADATA_SOURCE_KEY) or source_key),
        document_id=document_id_for(source_key),
        chunk_count=chunk_count,
        # What the last run said this file should have, against what it does
        # have. A disagreement is how an interrupted run becomes visible.
        chunk_total=_as_int(metadata.get(METADATA_CHUNK_TOTAL)),
        embedded_at=to_datetime(metadata.get(METADATA_EMBEDDED_AT)),
        source_last_modified=to_datetime(metadata.get(METADATA_SOURCE_LAST_MODIFIED)),
        source_etag=str(metadata.get(METADATA_SOURCE_ETAG) or ""),
        embedding_model=str(metadata.get(EMBEDDING_MODEL_METADATA_KEY) or ""),
    )


def _to_chunks(ids: list[str], records: dict[str, dict[str, Any]]) -> list[SourceChunk]:
    """Assemble a file's chunks from records already fetched, in document order.

    Args:
        ids: The file's vector ids.
        records: Fetched records, keyed by vector id.

    Returns:
        The chunks, ordered by position in the document.
    """
    chunks: list[SourceChunk] = []
    for vector_id in ids:
        metadata = _metadata_of(records.get(vector_id, {}))
        content = str(metadata.get(METADATA_CONTENT) or "")

        # Trust the stored index, but fall back to parsing it out of the id so
        # a vector written before the metadata existed still lists in order.
        stored_index = metadata.get(METADATA_CHUNK_INDEX)
        _, parsed_index = parse_vector_id(vector_id)
        chunk_index = int(stored_index) if stored_index is not None else (parsed_index or 0)

        chunks.append(
            SourceChunk(
                vector_id=vector_id,
                chunk_index=chunk_index,
                content=content,
                char_count=len(content),
            )
        )

    chunks.sort(key=lambda chunk: chunk.chunk_index)
    return chunks


async def read_document(
    source_key: str, ids: list[str], space: Optional[VectorSpace] = None
) -> tuple[Optional[IndexedDocument], list[SourceChunk]]:
    """Read one file's record and every chunk of it, from a known id list.

    Both halves come out of a single fetch. They used to be read separately,
    which meant listing the same file's ids twice and fetching its first chunk
    an extra time on every detail request.

    Args:
        source_key: The object key within the bucket.
        ids: The file's vector ids, as returned by `list_vector_ids_for`.
        space: Which index and namespace to read. Defaults to production.

    Returns:
        The index's record of the file and its chunks, or `(None, [])` when
        nothing is indexed for it.
    """
    if not ids:
        return None, []

    records = await asyncio.to_thread(fetch_vectors, ids, space)
    metadata = _metadata_of(records.get(ids[0], {}))

    return (
        _to_indexed_document(source_key, metadata, len(ids)),
        _to_chunks(ids, records),
    )


async def get_indexed_document(source_key: str) -> Optional[IndexedDocument]:
    """Return what the index holds for one source file.

    Args:
        source_key: The object key within the bucket.

    Returns:
        The index's record of the file, or None if nothing is indexed for it.
    """
    ids = await list_vector_ids_for(source_key)
    if not ids:
        return None

    # Every chunk carries the same provenance, so one is enough to describe the
    # document. Reading the first keeps this cheap on files with many chunks.
    records = await asyncio.to_thread(fetch_vectors, ids[:1])

    return _to_indexed_document(
        source_key, _metadata_of(records.get(ids[0], {})), len(ids)
    )


async def get_chunks(
    source_key: str, space: Optional[VectorSpace] = None
) -> list[SourceChunk]:
    """Return every chunk the index holds for one source file, in order.

    Args:
        source_key: The object key within the bucket.
        space: Which index and namespace to read. Defaults to production.

    Returns:
        The file's indexed chunks, ordered by position in the document.
    """
    ids = await list_vector_ids_for(source_key, space)
    if not ids:
        return []

    return _to_chunks(ids, await asyncio.to_thread(fetch_vectors, ids, space))


async def list_indexed_documents() -> dict[str, IndexedDocument]:
    """Describe every source file the index currently holds vectors for.

    Reading the whole index is what surfaces orphans — files whose vectors
    outlived the object they came from, which a storage-driven listing can
    never reveal.

    This is the expensive read in the pipeline, and the reason
    app.services.source_cache exists. It is still built to be as cheap as one
    pass can be: the id listing gives every document's chunk count for free,
    and only the first id of each document is fetched, so a corpus of a
    thousand chunks costs one listing and one batched fetch rather than a
    round trip per file.

    Returns:
        Each indexed source key mapped to the index's record of it.
    """
    # Listing with no prefix walks every vector id in the index.
    ids = await asyncio.to_thread(list_vector_ids, "")
    if not ids:
        return {}

    # One vector per document is enough to recover its provenance, and each
    # document's ids share a prefix — so keep the first id of each document,
    # counting the rest rather than fetching them.
    first_of_document: dict[str, str] = {}
    counts: dict[str, int] = {}
    for vector_id in sorted(ids):
        document_id, _ = parse_vector_id(vector_id)
        first_of_document.setdefault(document_id, vector_id)
        counts[document_id] = counts.get(document_id, 0) + 1

    records = await asyncio.to_thread(fetch_vectors, list(first_of_document.values()))

    documents: dict[str, IndexedDocument] = {}
    for document_id, vector_id in first_of_document.items():
        metadata = _metadata_of(records.get(vector_id, {}))
        source_key = str(metadata.get(METADATA_SOURCE_KEY) or "")
        # A vector with no source key cannot be joined back to a file, so it
        # is left out rather than listed under an invented one.
        if not source_key:
            continue
        documents[source_key] = _to_indexed_document(
            source_key, metadata, counts[document_id]
        )

    return documents


async def list_indexed_source_keys() -> set[str]:
    """List every source key that currently has vectors in the index.

    Returns:
        The distinct source keys the index holds.
    """
    return set(await list_indexed_documents())


async def delete_document(
    source_key: str, space: Optional[VectorSpace] = None
) -> int:
    """Remove every vector belonging to one source file.

    Args:
        source_key: The object key within the bucket.
        space: Which index and namespace to delete from. Defaults to
            production — a caller deleting an experiment's copy of a file has
            to say so, so a slip cannot empty the live index.

    Returns:
        How many vectors were deleted.
    """
    ids = await list_vector_ids_for(source_key, space)
    if not ids:
        return 0

    return await asyncio.to_thread(delete_vectors, ids, space)


async def prune_vectors(
    vector_ids: list[str], space: Optional[VectorSpace] = None
) -> int:
    """Delete a named set of vectors.

    Used by an indexing run to remove exactly the vectors its plan identified
    as obsolete, rather than inferring them from a chunk count.

    Args:
        vector_ids: The ids to remove.
        space: Which index and namespace they are in. Defaults to production.

    Returns:
        How many vectors were deleted.
    """
    if not vector_ids:
        return 0

    return await asyncio.to_thread(delete_vectors, vector_ids, space)


async def prune_chunks_beyond(source_key: str, chunk_count: int) -> int:
    """Delete a file's leftover chunks above its new chunk count.

    A file that shrinks from twelve chunks to eight leaves the last four
    behind: the upsert overwrites chunks 0-7 in place but never touches 8-11,
    so stale text would keep surfacing in retrieval results forever.

    Args:
        source_key: The object key within the bucket.
        chunk_count: How many chunks the file now has.

    Returns:
        How many leftover vectors were deleted.
    """
    ids = await list_vector_ids_for(source_key)

    stale = [
        vector_id
        for vector_id in ids
        for _, index in [parse_vector_id(vector_id)]
        # An unparseable id predates this scheme; leave it for an explicit
        # delete rather than guessing that it belongs to this document.
        if index is not None and index >= chunk_count
    ]
    if not stale:
        return 0

    return await asyncio.to_thread(delete_vectors, stale)
