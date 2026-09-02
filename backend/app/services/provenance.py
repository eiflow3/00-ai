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

# Length of a chunk's content fingerprint.  Short enough to read in a table,
# wide enough that two different chunks will not collide.
CONTENT_HASH_LENGTH = 16

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

# First and last source-document page a chunk's text touches. Written only for
# formats that have pages; absent — not zero — on everything else, because
# Pinecone rejects nulls and a page number on a .txt would be an invention.
METADATA_PAGE_START = "page_start"
METADATA_PAGE_END = "page_end"

# --- Derived artifacts --------------------------------------------------------
# A structured source (a PDF) is normalised once into canonical markdown, and
# that markdown — plus each extracted table — is stored *beside* the original,
# under this prefix. The prefix is what keeps the two worlds apart: everything
# under it is private plumbing, never listed as a source and never uploadable.

DERIVED_PREFIX = "derived/"

# The URI scheme table links use inside embedded text. The description that
# stands in for a table ends with a link of this shape, and the frontend
# resolves it back to the stored artifact.
TABLE_LINK_SCHEME = "table"

# Zero-padded so a listing of table artifacts sorts in document order.
_TABLE_ID_FORMAT = "table-{index:03d}"


def derived_markdown_key_for(source_key: str) -> str:
    """The object key holding a source file's canonical extracted markdown."""
    return f"{DERIVED_PREFIX}{document_id_for(source_key)}/document.md"


def derived_extraction_key_for(source_key: str) -> str:
    """The object key holding a source file's full extraction record."""
    return extraction_key_for_document(document_id_for(source_key))


def extraction_key_for_document(document_id: str) -> str:
    """The extraction-record key, addressed by document id.

    Table links carry only the document id, so their reads resolve keys this
    way round.
    """
    return f"{DERIVED_PREFIX}{document_id}/extraction.json"


def derived_prefix_for(source_key: str) -> str:
    """The prefix under which every derived artifact of one source file lives."""
    return f"{DERIVED_PREFIX}{document_id_for(source_key)}/"


def table_prefix_for(document_id: str) -> str:
    """The prefix under which one document's table artifacts live."""
    return f"{DERIVED_PREFIX}{document_id}/tables/"


def table_artifact_key_for(document_id: str, table_id: str) -> str:
    """The object key holding one extracted table's markdown."""
    return f"{DERIVED_PREFIX}{document_id}/tables/{table_id}.md"


def table_id_for(index: int) -> str:
    """The id of the nth table in a document, deterministic across re-runs.

    Args:
        index: Zero-based position of the table in document order.

    Returns:
        An id of the form "table-001".
    """
    return _TABLE_ID_FORMAT.format(index=index + 1)


def table_link_for(document_id: str, table_id: str, label: str) -> str:
    """Build the markdown link that stands in for a table in embedded text.

    This exact shape is what the chat frontend recognises and turns into a
    clickable view of the stored table — spelled here and nowhere else.

    Args:
        document_id: The document id from `document_id_for`.
        table_id: The table's id from `table_id_for`.
        label: The link text a reader sees.

    Returns:
        A markdown link of the form "[label](table://{document_id}/{table_id})".
    """
    return f"[{label}]({TABLE_LINK_SCHEME}://{document_id}/{table_id})"

# How many chunks the whole file should have.  Stamped on every chunk so that
# comparing it against the number of vectors actually present detects a run
# that stopped partway — a write that never finished, or a prune that never
# ran.  Without it a half-written file is indistinguishable from a complete
# one, because the fields above all describe the file, not the set of vectors.
METADATA_CHUNK_TOTAL = "chunk_total"


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


def content_fingerprint(content: str) -> str:
    """Fingerprint a chunk's text, so a later re-index can be told it changed.

    A vector id names a *slot* — the same `{document_id}#{nnnnn}` holds
    different text after a re-index at a different chunk size.  Anything that
    records what a chunk said therefore has to record this alongside the id, or
    it cannot tell a stable chunk from a silently replaced one.

    Args:
        content: The chunk's text.

    Returns:
        A short hex fingerprint of the text.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:CONTENT_HASH_LENGTH]


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
    chunk_total: int = 0,
    embedded_at: Optional[datetime] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
) -> dict[str, Any]:
    """Build the metadata stamped onto one chunk's vector.

    The source's etag and last-modified time are captured *as they were at the
    moment of embedding*.  That snapshot is the whole basis of staleness
    detection: comparing it against the object's current values is what reveals
    that a file changed after it was indexed.

    `chunk_total` serves the other half of that question.  The fields above all
    describe the *file*, so they cannot reveal that only some of its chunks were
    written; the expected total can, because the number of vectors present is
    already known from a prefix listing.

    Args:
        source: The object this chunk came from, as listed by the store.
        chunk_index: Zero-based position of the chunk within the file.
        content: The chunk's text, stored so retrieval can return it directly.
        chunk_total: How many chunks the whole file produced.
        embedded_at: When the vector was produced; defaults to now.
        page_start: First source-document page this chunk touches, if pages
            exist for this format. Omitted from the metadata when None.
        page_end: Last source-document page this chunk touches, likewise.

    Returns:
        A metadata dict using only types Pinecone accepts.
    """
    stamped_at = embedded_at or datetime.now(timezone.utc)

    # Pages are written only when known: Pinecone rejects nulls, and absence is
    # what marks a format that has no pages at all.
    pages: dict[str, Any] = {}
    if page_start is not None:
        pages[METADATA_PAGE_START] = page_start
    if page_end is not None:
        pages[METADATA_PAGE_END] = page_end

    return {
        **pages,
        # The join key — the one field that is authoritative on both sides.
        METADATA_SOURCE_KEY: source.key,
        METADATA_DOCUMENT_ID: document_id_for(source.key),
        METADATA_CHUNK_INDEX: chunk_index,
        METADATA_CHUNK_TOTAL: chunk_total,
        METADATA_CONTENT: content,
        # Snapshot of the source at embed time; drives the staleness verdict.
        METADATA_SOURCE_ETAG: source.etag,
        METADATA_SOURCE_LAST_MODIFIED: _to_epoch(source.last_modified),
        METADATA_EMBEDDED_AT: _to_epoch(stamped_at),
        # `embedding_model` is stamped by vector_store.upsert_chunks, which
        # owns that key so backfills of mixed-model batches stay possible.
    }
