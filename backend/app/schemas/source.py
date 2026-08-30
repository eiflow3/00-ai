"""Data Phase – Source: a file in object storage that we embed."""

from datetime import datetime

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
