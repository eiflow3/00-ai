"""Data Phase – Source: a file in object storage, and its embeddings.

Holds both sides of the data embedding pipeline and the comparison between
them: what exists in storage (SourceObject), what the vector index holds for
it (IndexedDocument), and the verdict that joins the two (SourceStatus).
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SourceObject(BaseModel):
    """A single file in the object store, as listed by the storage provider.

    This is the *origin* side of the pipeline: what exists to be embedded, and
    when it last changed.  Comparing `last_modified` here against the embedding
    timestamp stored in the vector index is what tells us an index is stale.
    """

    # Object key (path within the bucket), e.g. "policies/refunds.md".
    key: str = Field(..., description="Object key within the bucket")

    # When the object was last written in the store.
    last_modified: datetime = Field(
        ..., description="Timestamp of the object's last write in object storage"
    )

    # Size in bytes.
    size: int = Field(default=0, ge=0, description="Object size in bytes")

    # Provider-supplied content hash. Changes whenever the bytes change, so it
    # is a stronger staleness signal than a timestamp alone.
    etag: str = Field(default="", description="Content hash reported by the store")


class IndexState(str, Enum):
    """Where a source file stands relative to its embeddings.

    This is the verdict the client renders next to each file: whether what is
    in the vector index still reflects what is in object storage.
    """

    # In storage, but nothing has ever been embedded from it.
    NOT_INDEXED = "not_indexed"

    # Embedded, and the index still matches the stored bytes.
    CURRENT = "current"

    # The file changed in storage after it was embedded — re-index needed.
    STALE_CONTENT = "stale_content"

    # Embedded with a different model than the one now configured. Scores
    # across two embedding spaces are meaningless, so this needs re-indexing
    # just as urgently as changed content.
    STALE_MODEL = "stale_model"

    # Vectors exist for a file that is no longer in storage.
    ORPHANED = "orphaned"

    # In storage, but this pipeline has no extractor for its file type.
    UNSUPPORTED = "unsupported"


class IndexedDocument(BaseModel):
    """The vector index's record of one source file.

    This is the *index* side of the pipeline, and the counterpart to
    SourceObject. Every field here is a snapshot taken at embedding time, which
    is what makes staleness detectable: comparing these values against the
    live object reveals that the file moved on and its vectors did not.
    """

    # The object key these vectors were built from — the join key back to
    # storage. Stored on every vector, so this survives without a database.
    source_key: str = Field(..., description="Object key these vectors came from")

    # Derived id that prefixes every one of this file's vector ids.
    document_id: str = Field(..., description="Stable id derived from the source key")

    # How many vectors this file currently occupies in the index.
    chunk_count: int = Field(default=0, ge=0, description="Number of chunks indexed")

    # When the embeddings were written.
    embedded_at: Optional[datetime] = Field(
        default=None, description="When this file was last embedded"
    )

    # The source's last-modified time as it was at embedding time.
    source_last_modified: Optional[datetime] = Field(
        default=None,
        description="The object's last-modified time when it was embedded",
    )

    # The source's content hash as it was at embedding time. Compared against
    # the object's current etag to decide whether the index is stale.
    source_etag: str = Field(
        default="", description="The object's content hash when it was embedded"
    )

    # Which model produced these vectors.
    embedding_model: str = Field(
        default="", description="Embedding model that produced these vectors"
    )


class SourceStatus(BaseModel):
    """One source file, joined with its embeddings.

    The row behind the client's file list: both timestamps side by side, and a
    single verdict saying whether the embeddings need rebuilding.
    """

    # The object key. Present whether or not the file still exists in storage.
    source_key: str = Field(..., description="Object key within the bucket")

    # The verdict.
    state: IndexState = Field(..., description="Where this file stands versus its index")

    # The storage side. Absent when the file has been deleted but its vectors
    # remain, which is exactly the `orphaned` case.
    source: Optional[SourceObject] = Field(
        default=None, description="The file as it exists in object storage"
    )

    # The index side. Absent when nothing has been embedded from this file.
    indexed: Optional[IndexedDocument] = Field(
        default=None, description="What the vector index holds for this file"
    )

    # Plain-language reason for the verdict, so the client does not have to
    # reimplement the comparison rules to explain them.
    detail: str = Field(default="", description="Why this file is in this state")

    # Whether a run is embedding this file right now. Distinct from `state`,
    # which describes what is stored — this describes what is happening.
    indexing: bool = Field(
        default=False, description="True while a run is embedding this file"
    )

    @property
    def needs_reindex(self) -> bool:
        """Whether re-running the pipeline on this file would change anything."""
        return self.state in _REINDEXABLE_STATES


# States a re-index can actually fix. `orphaned` is excluded because there is
# no longer a file to embed — that one is resolved by deleting its vectors.
_REINDEXABLE_STATES = frozenset(
    {IndexState.NOT_INDEXED, IndexState.STALE_CONTENT, IndexState.STALE_MODEL}
)


class SourceChunk(BaseModel):
    """One indexed chunk of a source file, as stored in the vector index."""

    # The vector id, which encodes the document id and the chunk position.
    vector_id: str = Field(..., description="Vector id in the index")

    # Position of this chunk within its file.
    chunk_index: int = Field(default=0, ge=0, description="Position within the file")

    # The chunk's text, stored alongside the vector so retrieval can return it.
    content: str = Field(default="", description="Text content of the chunk")

    # Length of the chunk's text.
    char_count: int = Field(default=0, ge=0, description="Character count of the chunk")


class SourceDetail(BaseModel):
    """One source file in full — its status plus every chunk indexed from it."""

    # The same row the list endpoint returns.
    status: SourceStatus = Field(..., description="Storage and index state for this file")

    # Every chunk currently indexed, in document order.
    chunks: list[SourceChunk] = Field(
        default_factory=list, description="Indexed chunks, in document order"
    )


class DeindexResponse(BaseModel):
    """Result of removing a file's vectors from the index."""

    # The file whose vectors were removed.
    source_key: str = Field(..., description="Object key whose vectors were removed")

    # How many vectors were deleted. Zero means nothing was indexed.
    deleted: int = Field(default=0, ge=0, description="Number of vectors deleted")


class UploadResponse(BaseModel):
    """Result of writing a file into object storage.

    Returned by both upload and replace so a client handles one shape. The
    file's fresh status is included because a write changes which side of the
    pipeline knows about it — a caller should not have to re-list to find out.
    """

    # The file's state after the write. A new upload is `not_indexed`; a
    # replace is too, since replacing discards the old vectors.
    status: SourceStatus = Field(..., description="The file's state after the write")

    # Whether this overwrote an existing file rather than creating one.
    replaced: bool = Field(
        default=False, description="True when this overwrote an existing file"
    )

    # Whether this call is what put the object there. False when an identical
    # file was already stored, which makes a repeated upload a no-op rather
    # than a conflict.
    created: bool = Field(
        default=True, description="False when identical content was already stored"
    )

    # Vectors discarded because the file they described was replaced.
    pruned: int = Field(
        default=0,
        ge=0,
        description="Vectors removed because their source content was replaced",
    )
