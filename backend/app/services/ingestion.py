"""Ingestion — the data embedding pipeline, one file at a time.

The counterpart to app.services.retrieval.  Where retrieval runs

    query text -> embedding -> similarity search -> ranked chunks

this runs the other direction, turning one stored file into searchable vectors:

    object bytes -> text -> chunks -> plan -> embeddings -> upsert -> prune

Every vector written here is stamped with provenance (see
app.services.provenance) recording what the source file looked like at the
moment it was embedded, and how many chunks the file had.  That snapshot is
what later makes it possible to say a file has changed but its embeddings have
not — or that a run stopped halfway.

Two details are load-bearing and easy to undo by accident:

  * **Only outstanding chunks are embedded.**  app.services.index_plan compares
    the chunks the file produces now against the text already stored, so an
    interrupted run resumes instead of starting over.
  * **The first chunk is written last.**  It carries the fingerprint and the
    expected total that the staleness check reads, so writing it last means an
    interrupted write leaves the *old* values in place and the file honestly
    reports itself as stale.  Written first — as it was — a half-written file
    claims to be current while serving text from a version that no longer
    exists.

Selecting *which* files to run over, and looping across them, belongs to
app.services.index_queue.  This module handles one file and reports its stages.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from app.schemas.chunk import Chunk
from app.schemas.chunking import ChunkingConfig
from app.schemas.extraction import ExtractionResult
from app.schemas.source import SourceObject
from app.services import derived_artifacts, index_catalog, index_plan, table_describer
from app.services.chunker import chunk_document
from app.services.embeddings import embed_texts
from app.services.object_store import get_object, head_object
from app.services.provenance import build_metadata, vector_id_for
from app.services.text_extraction import extract_document, requires_derived_artifact
from app.services.vector_store import VectorSpace, upsert_chunks

logger = logging.getLogger(__name__)

# Chunks embedded per API call.  Batching is far cheaper than one call per
# chunk, but an unbounded batch risks the embedding endpoint's payload limit
# on long documents.
EMBED_BATCH_SIZE = 64

# The chunk whose metadata the staleness check reads. Written last, always.
PROVENANCE_CHUNK_INDEX = 0


class IngestionResult:
    """What happened to one file during a run.

    A small mutable record rather than a schema: it is internal bookkeeping
    that the queue turns into events, never something crossing the API.
    """

    def __init__(self, source_key: str) -> None:
        self.source_key = source_key
        self.chunk_count = 0
        self.embedded = 0
        self.reused = 0
        self.pruned = 0
        self.skipped = False
        self.error: Optional[str] = None
        # Non-fatal trouble along the way — a table whose description could
        # not be drafted, for instance. Reported as error events, never as a
        # failed run.
        self.warnings: list[str] = []


async def _embed_in_batches(texts: list[str], model: str) -> list[list[float]]:
    """Embed every text, one batch per API call.

    Args:
        texts: The chunk texts, in the order they will be written.
        model: The embedding model to use.

    Returns:
        Vectors in the same order as the input texts.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        logger.debug("Embedding %d chunk(s) with %s", len(batch), model)
        vectors.extend(await embed_texts(batch, model=model))
    return vectors


def _write_order(positions: list[int]) -> list[int]:
    """Order chunk positions for writing, with the provenance chunk last.

    Args:
        positions: The chunk positions to be written, in any order.

    Returns:
        The same positions, ascending, but with chunk 0 moved to the end so it
        lands only after every other chunk of this version has been stored.
    """
    ordered = sorted(position for position in positions if position != PROVENANCE_CHUNK_INDEX)
    if PROVENANCE_CHUNK_INDEX in positions:
        ordered.append(PROVENANCE_CHUNK_INDEX)
    return ordered


def _records_for(
    source: SourceObject,
    chunks: dict[int, Chunk],
    positions: list[int],
    vectors: list[list[float]],
    chunk_total: int,
    embedded_at: datetime,
) -> list[dict]:
    """Build the upsert payload for the positions just embedded."""
    return [
        {
            # The chunk id *is* the vector id — one identity across both sides.
            "id": vector_id_for(chunks[position].document_id, position),
            "values": vector,
            "metadata": build_metadata(
                source=source,
                chunk_index=position,
                content=chunks[position].content,
                chunk_total=chunk_total,
                embedded_at=embedded_at,
                page_start=chunks[position].page_start,
                page_end=chunks[position].page_end,
            ),
        }
        for position, vector in zip(positions, vectors)
    ]


async def index_source(
    source: SourceObject,
    config: ChunkingConfig,
    embedding_model: str,
    force: bool = False,
    space: Optional[VectorSpace] = None,
) -> AsyncIterator[tuple[str, IngestionResult]]:
    """Embed one source file, yielding after each stage completes.

    Args:
        source: The object to index, as listed by the store.
        config: How to cut the file — which strategy, at what size and overlap.
        embedding_model: Model used to embed the chunks.
        force: Re-embed every chunk even if the index already holds it.
        space: Where the vectors go. Defaults to production; a chunking
            experiment passes its own namespace so it cannot overwrite the
            vectors the app answers from.

    Yields:
        The name of each stage as it finishes, with the running result.

    Raises:
        UnsupportedSourceType: If no extractor handles this file type.
    """
    result = IngestionResult(source.key)

    # --- Load -------------------------------------------------------------
    data = await get_object(source.key)
    yield "loading", result

    # --- Extract ------------------------------------------------------------
    # A stored extraction that still matches the file's etag is the record
    # that this — the expensive step, minutes for a scanned PDF — already
    # happened. Reused even on a forced run: force re-embeds vectors, it does
    # not make unchanged bytes extract differently.
    extraction: Optional[ExtractionResult] = None
    persisted = requires_derived_artifact(source.key)
    if persisted:
        extraction = await derived_artifacts.load_extraction(source.key)

    freshly_extracted = extraction is None
    if extraction is None:
        extraction = await asyncio.to_thread(extract_document, source.key, data)
    yield "extracting", result

    # --- Describe tables ----------------------------------------------------
    # Each table found is swapped for LLM prose ending in a link to the stored
    # table artifact — a no-op for formats without tables, but always yielded
    # so every run reports the same stages. Runs only on a fresh extraction: a
    # reused one was described (and persisted) the first time. The artifact is
    # written after the splice, so what is stored is what gets chunked.
    if persisted and freshly_extracted:
        extraction, warnings = await table_describer.describe_tables(
            extraction, source.key
        )
        result.warnings.extend(warnings)
        await derived_artifacts.save(source, extraction)
    yield "describing_tables", result

    # --- Chunk ------------------------------------------------------------
    chunks = await chunk_document(
        source.key,
        extraction.text,
        config,
        embedding_model=embedding_model,
        pages=extraction.pages or None,
    )
    result.chunk_count = len(chunks)
    yield "chunking", result

    # A file with no readable text still counts as indexed — but it has no
    # vectors, so clear whatever a previous run left behind for it.
    if not chunks:
        result.pruned = await index_catalog.delete_document(source.key, space)
        logger.info("%s: no readable text; removed %d vector(s)", source.key, result.pruned)
        yield "upserting", result
        return

    # --- Plan -------------------------------------------------------------
    # What the index already holds correctly is work this run does not repeat.
    plan = await index_plan.plan_for(
        source.key, chunks, embedding_model, force=force, space=space
    )
    result.reused = plan.reused
    result.embedded = len(plan.embed)

    by_position = {chunk.chunk_index: chunk for chunk in chunks}
    positions = _write_order(plan.embed)

    # --- Embed ------------------------------------------------------------
    vectors: list[list[float]] = []
    if positions:
        vectors = await _embed_in_batches(
            [by_position[position].content for position in positions], embedding_model
        )
    yield "embedding", result

    # --- Upsert -----------------------------------------------------------
    if positions:
        # Timestamped once for the whole file, so every chunk written in this
        # pass reports the same embedding time.
        embedded_at = datetime.now(timezone.utc)

        records = _records_for(
            source=source,
            chunks=by_position,
            positions=positions,
            vectors=vectors,
            chunk_total=len(chunks),
            embedded_at=embedded_at,
        )

        await asyncio.to_thread(upsert_chunks, records, embedding_model, space)

    # Vectors the file no longer produces: a shrunken tail, or ids from a
    # geometry that no longer applies. Deleted after the write, so the document
    # is never briefly missing a chunk it does have.
    result.pruned = await index_catalog.prune_vectors(plan.prune, space)

    logger.info(
        "%s: %d chunk(s) — %d embedded, %d reused, %d pruned",
        source.key,
        result.chunk_count,
        result.embedded,
        result.reused,
        result.pruned,
    )
    yield "upserting", result


async def resolve_source(source_key: str) -> Optional[SourceObject]:
    """Fetch an object's metadata, or None when it no longer exists.

    Used when a run reaches a file that was named earlier: the queue may have
    been waiting long enough for the object to have been deleted since.
    """
    try:
        return await head_object(source_key)
    except FileNotFoundError:
        return None
