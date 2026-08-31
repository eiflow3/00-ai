"""Index plan — decides which of a file's chunks still need embedding.

Re-indexing a file used to mean re-embedding all of it.  That is wasteful in
the ordinary case (a run interrupted at chunk 400 of 500) and actively
expensive in the common one (a file re-indexed after a model or geometry change
that in fact altered nothing).

The index is already a record of what has been done — every chunk is stored
with its text, at a deterministic id.  So the work still outstanding is simply
the difference between the chunks the file produces now and the chunks the
index already holds:

    chunk i is already done  <=>  the vector at position i holds identical text
                                  and was built by the requested model

Comparing the text itself, rather than a fingerprint of the file, is what makes
this exact.  A partially written document can hold chunks from two different
versions of the file at once, and no per-file field can tell those apart —
whereas identical text at the same position is proof, whatever happened before.

This module only reads.  The decision to write is the caller's.
"""

import asyncio
import logging
from typing import Optional

from app.schemas.chunk import Chunk
from app.services.embeddings import EMBEDDING_MODEL_METADATA_KEY
from app.services.index_catalog import list_vector_ids_for
from app.services.provenance import (
    METADATA_CONTENT,
    parse_vector_id,
    vector_id_for,
)
from app.services.vector_store import fetch_vectors

logger = logging.getLogger(__name__)


class ChunkPlan:
    """What a run must do to bring one file's vectors up to date.

    Internal bookkeeping rather than a schema: it never crosses the API, and
    the counts a client sees are reported through the ingestion events.
    """

    def __init__(
        self,
        document_id: str,
        embed: list[int],
        reuse: list[int],
        prune: list[str],
    ) -> None:
        # Positions that must be embedded and written.
        self.embed = embed
        # Positions already correct in the index, and therefore skipped.
        self.reuse = reuse
        # Vector ids that should no longer exist — a shrunken file's tail, or
        # leftovers from a geometry that no longer applies.
        self.prune = prune
        self.document_id = document_id

    @property
    def reused(self) -> int:
        """How many chunks did not need re-embedding."""
        return len(self.reuse)

    @property
    def complete(self) -> bool:
        """Whether the index already holds exactly this file, chunk for chunk."""
        return not self.embed and not self.prune


async def plan_for(
    source_key: str,
    chunks: list[Chunk],
    embedding_model: str,
    force: bool = False,
) -> ChunkPlan:
    """Work out which chunks need embedding and which vectors are obsolete.

    Args:
        source_key: The object key being indexed.
        chunks: The chunks the file produces now, in document order.
        embedding_model: The model this run will embed with.
        force: Re-embed every chunk regardless of what the index holds. The
            escape hatch for a suspect index, at full cost.

    Returns:
        The positions to embed, the positions to reuse, and the vector ids to
        delete.
    """
    document_id = chunks[0].document_id if chunks else ""
    wanted = {chunk.chunk_index: chunk for chunk in chunks}

    existing_ids = await list_vector_ids_for(source_key)

    # Nothing stored, or the caller insists: everything is outstanding.
    if force or not existing_ids:
        return ChunkPlan(
            document_id=document_id,
            embed=sorted(wanted),
            reuse=[],
            prune=[] if force else list(existing_ids),
        )

    # Fetching returns each chunk's stored text, which is what the comparison
    # runs on. It costs one request per hundred vectors, against one embedding
    # call per sixty-four chunks — so on any file with reusable chunks this is
    # the cheaper of the two, and on a file with none it is one wasted read.
    records = await asyncio.to_thread(fetch_vectors, existing_ids)

    reuse: list[int] = []
    prune: list[str] = []

    for vector_id in existing_ids:
        _, index = parse_vector_id(vector_id)

        # An id this scheme cannot read, or a position the file no longer has:
        # either way the vector describes text that is not in the file now.
        if index is None or index not in wanted:
            prune.append(vector_id)
            continue

        if _matches(records.get(vector_id), wanted[index], embedding_model):
            reuse.append(index)
        # A position that exists but does not match is not pruned — the upsert
        # about to be written overwrites it in place, and deleting it first
        # would leave a window where the file is missing a chunk it has.

    embed = sorted(set(wanted) - set(reuse))

    # The first chunk carries the expected chunk total, and the completeness
    # check reads it from there. So any run that changes how many vectors the
    # file has must rewrite that chunk, even when its own text is unchanged —
    # otherwise a file that merely grew would carry an out-of-date total and
    # report itself interrupted forever. One chunk is a negligible cost for an
    # invariant the whole staleness check rests on.
    if (embed or prune) and 0 in wanted and 0 not in embed:
        embed.insert(0, 0)
        reuse = [index for index in reuse if index != 0]

    logger.debug(
        "%s: %d chunk(s) to embed, %d reusable, %d to prune",
        source_key,
        len(embed),
        len(reuse),
        len(prune),
    )

    return ChunkPlan(
        document_id=document_id, embed=embed, reuse=sorted(reuse), prune=prune
    )


def _matches(
    record: Optional[dict], chunk: Chunk, embedding_model: str
) -> bool:
    """Whether a stored vector already holds exactly this chunk.

    Args:
        record: The stored vector, or None if the fetch did not return it.
        chunk: The chunk the file produces at that position now.
        embedding_model: The model this run embeds with.

    Returns:
        True only when the stored text is identical and the stored vector came
        from the same model. A vector with no model recorded is not reused: it
        predates the stamp, so its embedding space is unknown, and reusing it
        would risk mixing two spaces in one document.
    """
    if not record:
        return False

    metadata = record.get("metadata") or {}

    if str(metadata.get(EMBEDDING_MODEL_METADATA_KEY) or "") != embedding_model:
        return False

    return str(metadata.get(METADATA_CONTENT) or "") == chunk.content


def vector_ids_for_positions(document_id: str, positions: list[int]) -> list[str]:
    """Build the vector ids for a set of chunk positions.

    Kept here so a caller working from a plan never spells an id format out
    itself — that rule belongs to app.services.provenance alone.
    """
    return [vector_id_for(document_id, position) for position in positions]
