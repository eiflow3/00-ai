"""Provenance — the link between an object in storage and its vectors.

One source file becomes many vectors.  This module owns *every* rule that
connects the two sides, so nothing else in the codebase has to spell them out:

    source key  ->  document id  ->  vector ids
    vector      ->  metadata     ->  source key

The link is deliberately derivable rather than stored.  There is no join table
and no third database: a source key hashes to a stable document id, that id
prefixes every one of the file's vector ids, and the key itself rides along in
each vector's metadata.  Both directions work from data we already hold.

Keeping this in one module is what makes re-indexing idempotent — the same file
always produces the same vector ids, so an upsert overwrites in place instead
of accumulating duplicates.
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from app.schemas.source import SourceObject

# Length of the hashed document id.  16 hex characters is 64 bits — collisions
# are not a practical concern for a document corpus, and short ids keep the
# vector id readable in logs and in the Pinecone console.
DOCUMENT_ID_LENGTH = 16

# Separator between the document id and the chunk index inside a vector id.
# Chosen because it cannot appear in a hex digest, so parsing back is exact.
VECTOR_ID_SEPARATOR = "#"

# Zero-padding for the chunk index.  Padding keeps vector ids in chunk order
# when sorted lexically, which is how Pinecone returns a prefix listing.
CHUNK_INDEX_WIDTH = 5

# --- Metadata keys ----------------------------------------------------------
# Written on upsert, read back when computing sync status.  Named here so the
# write side and the read side cannot drift apart on a spelling.

METADATA_SOURCE_KEY = "source_key"
METADATA_DOCUMENT_ID = "document_id"
METADATA_CHUNK_INDEX = "chunk_index"
METADATA_CONTENT = "content"
METADATA_SOURCE_ETAG = "source_etag"
METADATA_SOURCE_LAST_MODIFIED = "source_last_modified"
METADATA_EMBEDDED_AT = "embedded_at"


def document_id_for(source_key: str) -> str:
    """Derive the stable document id for an object key.

    Object keys contain slashes, spaces and unicode; vector ids need to be
    short and ASCII.  Hashing gives us both, and stays stable across runs so a
    re-index targets the same vectors it wrote last time.

    Args:
        source_key: The object key within the bucket.

    Returns:
        A 16-character hex id derived from the key.
    """
    digest = hashlib.sha1(source_key.encode("utf-8")).hexdigest()
    return digest[:DOCUMENT_ID_LENGTH]


def vector_id_for(document_id: str, chunk_index: int) -> str:
    """Build the vector id for one chunk of a document.

    Args:
        document_id: The document id from `document_id_for`.
        chunk_index: Zero-based position of the chunk within the file.

    Returns:
        An id of the form "a1b2c3d4e5f6a7b8#00003".
    """
    return f"{document_id}{VECTOR_ID_SEPARATOR}{chunk_index:0{CHUNK_INDEX_WIDTH}d}"


def vector_id_prefix_for(source_key: str) -> str:
    """Return the prefix that matches every vector belonging to a source file.

    This is what makes deletion possible on Pinecone serverless, which has no
    delete-by-metadata-filter: listing by this prefix produces exactly the id
    list a delete call needs.

    Args:
        source_key: The object key within the bucket.

    Returns:
        The vector id prefix, including the trailing separator.
    """
    return f"{document_id_for(source_key)}{VECTOR_ID_SEPARATOR}"


def parse_vector_id(vector_id: str) -> tuple[str, Optional[int]]:
    """Split a vector id back into its document id and chunk index.

    Args:
        vector_id: An id previously produced by `vector_id_for`.

    Returns:
        The document id, and the chunk index when the id carries a readable
        one.  Ids written before this scheme (or by hand) yield None rather
        than raising, so an older index stays inspectable.
    """
    document_id, separator, index = vector_id.partition(VECTOR_ID_SEPARATOR)
    if not separator or not index.isdigit():
        return vector_id, None
    return document_id, int(index)


def _to_epoch(value: datetime) -> float:
    """Convert a datetime to epoch seconds, assuming UTC when naive.

    Pinecone metadata accepts only strings, numbers, booleans and string
    lists.  Numbers are used for timestamps rather than ISO strings because a
    number can be range-filtered later; a string cannot.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def to_datetime(value: Any) -> Optional[datetime]:
    """Read a stored epoch timestamp back into an aware datetime.

    Args:
        value: A metadata value that should hold epoch seconds.

    Returns:
        The corresponding UTC datetime, or None if the value is missing or
        unreadable — a partially written vector should not break a listing.
    """
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def build_metadata(
    source: SourceObject,
    chunk_index: int,
    content: str,
    embedded_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build the metadata stamped onto one chunk's vector.

    The source's etag and last-modified time are captured *as they were at the
    moment of embedding*.  That snapshot is the whole basis of staleness
    detection: comparing it against the object's current values is what reveals
    that a file changed after it was indexed.

    Args:
        source: The object this chunk came from, as listed by the store.
        chunk_index: Zero-based position of the chunk within the file.
        content: The chunk's text, stored so retrieval can return it directly.
        embedded_at: When the vector was produced; defaults to now.

    Returns:
        A metadata dict using only types Pinecone accepts.
    """
    stamped_at = embedded_at or datetime.now(timezone.utc)

    return {
        # The join key — the one field that is authoritative on both sides.
        METADATA_SOURCE_KEY: source.key,
        METADATA_DOCUMENT_ID: document_id_for(source.key),
        METADATA_CHUNK_INDEX: chunk_index,
        METADATA_CONTENT: content,
        # Snapshot of the source at embed time; drives the staleness verdict.
        METADATA_SOURCE_ETAG: source.etag,
        METADATA_SOURCE_LAST_MODIFIED: _to_epoch(source.last_modified),
        METADATA_EMBEDDED_AT: _to_epoch(stamped_at),
        # `embedding_model` is stamped by vector_store.upsert_chunks, which
        # owns that key so backfills of mixed-model batches stay possible.
    }
