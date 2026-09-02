"""Deletion service — the rules governing removal from either side.

Removing is the mirror of uploading, and it has the same property: the two
sides of the pipeline can be taken out of step on purpose.  A caller therefore
has to say *which* removal it means, and the two answers are genuinely
different operations:

  * vectors only — the file stays in storage and can be embedded again, which
    is how a document is withdrawn from retrieval without losing it;
  * file and vectors — the document goes away entirely.

Both are ordered vectors-first.  If the object delete then fails, what is left
is a file with no embeddings: it reads as `not_indexed`, and re-indexing fixes
it.  The other order can leave vectors describing a file that no longer exists,
and those get cited with full confidence — the same reasoning that makes a
replace prune before it returns.

Neither is allowed while the pipeline is holding the file.  A worker mid-write
would otherwise finish writing vectors for a document that has just been
deleted, and nothing later would reveal where they came from.
"""

import logging

from app.services import (
    chunk_variants,
    derived_artifacts,
    index_catalog,
    index_registry,
    source_cache,
)
from app.services.object_store import delete_object

logger = logging.getLogger(__name__)


class DeletionBlocked(RuntimeError):
    """Raised when a file cannot be deleted yet.

    Carries a message written for the person who clicked delete, since it is
    shown to them verbatim.
    """


def _guard(source_key: str) -> None:
    """Refuse a deletion the indexing pipeline is not finished with.

    Args:
        source_key: The object key being deleted.

    Raises:
        DeletionBlocked: While a run is embedding the file or has it queued.
    """
    if index_registry.is_indexing(source_key):
        raise DeletionBlocked(
            f"{source_key!r} is being embedded right now. "
            f"Stop the run first, then delete it."
        )

    if index_registry.is_queued(source_key):
        raise DeletionBlocked(
            f"{source_key!r} is waiting in the indexing queue. "
            f"Stop the run first, then delete it."
        )


async def delete_vectors(source_key: str) -> int:
    """Remove a file's embeddings, leaving the file itself in storage.

    Args:
        source_key: The object key whose vectors should be removed.

    Returns:
        How many vectors were deleted. Zero means nothing was indexed under
        that key, which is not an error — it is the end state asked for.

    Raises:
        DeletionBlocked: While the pipeline is holding the file.
    """
    _guard(source_key)

    deleted = await index_catalog.delete_document(source_key)
    await source_cache.invalidate(source_key)

    logger.info("deindexed %s: %d vector(s) removed", source_key, deleted)

    return deleted


async def delete_source(source_key: str) -> tuple[int, bool]:
    """Remove a file and every embedding built from it.

    Args:
        source_key: The object key to delete.

    Returns:
        How many vectors were deleted, and whether an object was actually
        removed from storage. Both can be zero-ish: deleting a key that is
        already gone from both sides reaches the same end state, so it is
        reported rather than raised. The count includes any copies held by a
        chunking variant.

    Raises:
        DeletionBlocked: While the pipeline is holding the file.
    """
    _guard(source_key)

    # Vectors first: a failure after this point leaves a file that reads as
    # `not_indexed`, which is recoverable, rather than orphaned vectors.
    deleted = await index_catalog.delete_document(source_key)

    # And the same file's copies inside every chunking experiment. Deindexing
    # deliberately does not do this — that withdraws a file from retrieval and
    # leaves it re-indexable — but a file that is *gone* leaves nothing behind
    # anywhere, or a comparison run would score four strategies against text
    # nobody can look up any more.
    deleted += await chunk_variants.forget_source(source_key)

    removed = await delete_object(source_key)

    # A file that is gone leaves nothing extracted from it behind either.
    # Deindexing deliberately keeps these — the file is still there and its
    # extraction still describes it — but a deleted file's artifacts would
    # never be read again, only found.
    await derived_artifacts.delete_for(source_key)

    await source_cache.invalidate(source_key)

    logger.info(
        "deleted %s: file %s, %d vector(s) removed",
        source_key,
        "removed" if removed else "already absent",
        deleted,
    )

    return deleted, removed
