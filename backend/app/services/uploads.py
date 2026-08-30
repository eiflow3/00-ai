"""Upload service — the rules governing a write into object storage.

Storing a file is the one operation that can put the two sides of the pipeline
out of step on purpose, so the rules live here rather than in the router:

  * a new upload creates an object and nothing else — indexing stays a separate,
    deliberate step;
  * a replace overwrites the bytes *and* deletes every vector built from the old
    ones, because chunks describing content that no longer exists are worse than
    no chunks at all — a model cites them with full confidence.

Only file types the pipeline can actually read are accepted, so the file list
never fills with rows that indexing will always skip.
"""

import hashlib
from pathlib import PurePosixPath

from app.config import settings
from app.schemas.source import SourceObject
from app.services import index_catalog
from app.services.object_store import head_object, put_object
from app.services.text_extraction import SUPPORTED_EXTENSIONS, is_supported

# MIME type recorded on stored objects. Everything we accept is text, and the
# extension is what actually selects an extractor.
DEFAULT_CONTENT_TYPE = "text/plain; charset=utf-8"

# Path segments that would let a key escape its prefix.
_TRAVERSAL_SEGMENTS = {"..", "."}


class UploadRejected(ValueError):
    """Raised when a file may not be written.

    Carries a message written for the person who chose the file, since it is
    shown to them verbatim.
    """


def normalise_key(filename: str, prefix: str = "") -> str:
    """Build a safe object key from an uploaded filename and optional prefix.

    Args:
        filename: The name the browser reported for the file.
        prefix: Folder to place it under, if any.

    Returns:
        The key to store the object at.

    Raises:
        UploadRejected: If the name is empty, or tries to escape its prefix.
    """
    # Browsers can send a full path; only the final component is the name.
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name:
        raise UploadRejected("The file has no usable name.")

    parts = [segment for segment in f"{prefix.strip('/')}/{name}".split("/") if segment]

    # A key is a path within one bucket, so a traversal segment is never
    # meaningful — it can only be an attempt to write outside the prefix.
    if any(segment in _TRAVERSAL_SEGMENTS for segment in parts):
        raise UploadRejected("The file path may not contain '.' or '..' segments.")

    return "/".join(parts)


def validate(key: str, data: bytes) -> None:
    """Check a file may be written, raising with a readable reason if not.

    Args:
        key: The object key the file would be stored at.
        data: The file's raw contents.

    Raises:
        UploadRejected: If the type is unreadable, or the size is unusable.
    """
    if not is_supported(key):
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UploadRejected(
            f"Only {supported} files can be indexed, so other types are not accepted."
        )

    if not data:
        raise UploadRejected("The file is empty.")

    if len(data) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes / (1024 * 1024)
        raise UploadRejected(f"The file is larger than the {limit_mb:.0f} MB limit.")


async def find(key: str) -> SourceObject | None:
    """Return the object at this key, or None if nothing is there."""
    try:
        return await head_object(key)
    except FileNotFoundError:
        return None


def is_same_content(stored: SourceObject, data: bytes) -> bool:
    """Whether the stored object already holds exactly these bytes.

    A single-part upload's etag is the MD5 of its contents, so comparing it
    against the incoming bytes identifies a repeat of an upload that already
    succeeded. An object written as a multipart upload — anything put here
    through the storage provider's own console, for instance — carries a
    "-<parts>" suffix instead and will not match, so those fall through to
    being treated as a genuine conflict rather than being wrongly merged.

    Args:
        stored: The object currently at the key.
        data: The bytes being uploaded.

    Returns:
        True only when the stored object is provably the same content.
    """
    if not stored.etag or "-" in stored.etag:
        return False

    return hashlib.md5(data).hexdigest() == stored.etag


async def upload_new(
    filename: str, data: bytes, prefix: str = ""
) -> tuple[SourceObject, bool]:
    """Store a file at a key that must not already hold different content.

    Uploading the identical file twice is treated as the same upload, not a
    conflict: a client whose connection dropped after the object was written
    has no way to know it succeeded, and retrying should reach the state it
    asked for rather than an error about its own earlier attempt.

    Args:
        filename: The name the browser reported for the file.
        data: The file's raw contents.
        prefix: Folder to place it under, if any.

    Returns:
        The stored object, and whether this call created it.

    Raises:
        UploadRejected: If the file is unacceptable, or the key holds different
            content — which is a real collision, and needs a replace.
    """
    key = normalise_key(filename, prefix)
    validate(key, data)

    stored = await find(key)
    if stored is not None:
        # The same bytes are already there, so the caller's intent is met.
        if is_same_content(stored, data):
            return stored, False

        # Different content under a taken name is a genuine collision.
        # Overwriting is a separate, explicit action because it also discards
        # that file's embeddings.
        raise UploadRejected(
            f"A different file already exists at {key!r}. "
            f"Replace it instead of uploading over it."
        )

    return await put_object(key, data, DEFAULT_CONTENT_TYPE), True


async def replace(
    source_key: str, data: bytes, filename: str = ""
) -> tuple[SourceObject, int]:
    """Overwrite one file's contents and discard the vectors built from them.

    The key is fixed by the caller, not taken from the uploaded filename — a
    replace targets an existing row, so its identity is the row's, whatever the
    chosen file happens to be called.

    The uploaded file's own type is still checked. Ignoring it would let a
    binary be stored under a .md key and then chunked as though it were text.

    Args:
        source_key: The object key to overwrite.
        data: The new contents.
        filename: The uploaded file's name, checked for a readable type.

    Returns:
        The stored object, and how many vectors were pruned.

    Raises:
        UploadRejected: If the new contents are unacceptable.
    """
    validate(source_key, data)

    # The name is only a type check here; it never becomes the key.
    if filename:
        validate(PurePosixPath(filename.replace("\\", "/")).name, data)

    # Write first, then prune. If the write fails the old file and its vectors
    # are both still intact, rather than the vectors being gone for nothing.
    stored = await put_object(source_key, data, DEFAULT_CONTENT_TYPE)

    # Every existing vector describes bytes that no longer exist.
    pruned = await index_catalog.delete_document(source_key)

    return stored, pruned
