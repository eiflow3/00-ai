"""Ingestion — the data embedding pipeline.

The counterpart to app.services.retrieval.  Where retrieval runs

    query text -> embedding -> similarity search -> ranked chunks

this runs the other direction, turning stored files into searchable vectors:

    object bytes -> text -> chunks -> embeddings -> upsert -> prune

Every vector written here is stamped with provenance (see
app.services.provenance) recording what the source file looked like at the
moment it was embedded.  That snapshot is what later makes it possible to say
a file has changed but its embeddings have not.

The run is exposed as an async generator of typed events rather than a single
return value: indexing a batch takes long enough that a caller needs to see
progress, and one unreadable file must not abort the rest of the run.
"""

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from app.config import settings
from app.schemas.ingestion import (
    IndexCompletedEvent,
    IndexCompletedEventData,
    IndexErrorEvent,
    IndexErrorEventData,
    IndexProgressEvent,
    IndexProgressEventData,
    IndexRequest,
    IndexStartedEvent,
    IndexStartedEventData,
    IndexSummaryEvent,
    IndexSummaryEventData,
)
from app.schemas.source import IndexState, SourceObject, SourceStatus
from app.services import index_catalog, index_registry, sync_status
from app.services.chunker import chunk_document
from app.services.embeddings import embed_texts
from app.services.object_store import get_object, head_object
from app.services.provenance import build_metadata, vector_id_for
from app.services.text_extraction import UnsupportedSourceType, extract_text
from app.services.vector_store import upsert_chunks

# Chunks embedded per API call.  Batching is far cheaper than one call per
# chunk, but an unbounded batch risks the embedding endpoint's payload limit
# on long documents.
EMBED_BATCH_SIZE = 64


class IngestionResult:
    """What happened to one file during a run.

    A small mutable record rather than a schema: it is internal bookkeeping
    that the generator turns into events, never something crossing the API.
    """

    def __init__(self, source_key: str) -> None:
        self.source_key = source_key
        self.chunk_count = 0
        self.pruned = 0
        self.skipped = False
        self.error: Optional[str] = None


async def _embed_in_batches(texts: list[str], model: str) -> list[list[float]]:
    """Embed every chunk, one batch per API call.

    Args:
        texts: The chunk texts, in document order.
        model: The embedding model to use.

    Returns:
        Vectors in the same order as the input texts.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        vectors.extend(await embed_texts(batch, model=model))
    return vectors


async def index_source(
    source: SourceObject,
    chunk_size: int,
    chunk_overlap: int,
    embedding_model: str,
) -> AsyncIterator[tuple[str, IngestionResult]]:
    """Embed one source file, yielding after each stage completes.

    Args:
        source: The object to index, as listed by the store.
        chunk_size: Maximum tokens per chunk.
        chunk_overlap: Tokens repeated between consecutive chunks.
        embedding_model: Model used to embed the chunks.

    Yields:
        The name of each stage as it finishes, with the running result.

    Raises:
        UnsupportedSourceType: If no extractor handles this file type.
    """
    result = IngestionResult(source.key)

    # --- Load -------------------------------------------------------------
    data = await get_object(source.key)
    text = extract_text(source.key, data)
    yield "loading", result

    # --- Chunk ------------------------------------------------------------
    chunks = chunk_document(source.key, text, chunk_size, chunk_overlap)
    result.chunk_count = len(chunks)
    yield "chunking", result

    # A file with no readable text still counts as indexed — but it has no
    # vectors, so clear whatever a previous run left behind for it.
    if not chunks:
        result.pruned = await index_catalog.delete_document(source.key)
        yield "upserting", result
        return

    # --- Embed ------------------------------------------------------------
    vectors = await _embed_in_batches([chunk.content for chunk in chunks], embedding_model)
    yield "embedding", result

    # --- Upsert -----------------------------------------------------------
    # Timestamped once for the whole file, so every chunk of one document
    # reports the same embedding time.
    embedded_at = datetime.now(timezone.utc)

    records = [
        {
            # The chunk id is the vector id — one identity across both sides.
            "id": vector_id_for(chunk.document_id, chunk.chunk_index),
            "values": vector,
            "metadata": build_metadata(
                source=source,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                embedded_at=embedded_at,
            ),
        }
        for chunk, vector in zip(chunks, vectors)
    ]

    await asyncio.to_thread(upsert_chunks, records, embedding_model)

    # A file that shrank leaves its old tail behind: the upsert overwrites the
    # chunks it still has, but never touches the ones it no longer produces.
    result.pruned = await index_catalog.prune_chunks_beyond(source.key, len(chunks))
    yield "upserting", result


async def run(request: IndexRequest) -> AsyncIterator[object]:
    """Run the data embedding pipeline, streaming typed progress events.

    Args:
        request: Which files to process, and how to chunk and embed them.

    Yields:
        Event models: one `started`, then `progress`/`completed`/`error` per
        file, then a single `summary`.
    """
    # --- Decide what to process -------------------------------------------
    if request.keys:
        # Explicit keys are honoured as given; the caller has already chosen.
        selected = [await _describe(key) for key in request.keys]
        sources = [source for source in selected if source is not None]
        missing = [
            key for key, source in zip(request.keys, selected) if source is None
        ]
    else:
        # Otherwise let the storage-versus-index comparison pick the work.
        statuses = await sync_status.list_reindexable(
            prefix=request.prefix,
            only_stale=request.only_stale and not request.force,
        )
        sources = [status.source for status in statuses if status.source]
        missing = []

    # Claim the files before announcing them. Two runs on one file interleave
    # their upserts and prunes into an index matching neither version, so a key
    # another run already holds is left to that run rather than raced for.
    claimed, busy = await index_registry.claim([source.key for source in sources])

    # Everything past the claim is guarded, so a client disconnecting at any
    # point — which cancels this generator — still gives the keys back.
    try:
        claimed_set = set(claimed)
        sources = [source for source in sources if source.key in claimed_set]

        yield IndexStartedEvent(
            data=IndexStartedEventData(
                keys=[source.key for source in sources],
                total=len(sources),
                embedding_model=request.embedding_model,
                busy=busy,
            )
        )

        # Keys the caller named that are not in the bucket: reported up front
        # so the run's totals still add up.
        for key in missing:
            yield IndexErrorEvent(
                data=IndexErrorEventData(
                    source_key=key,
                    stage="loading",
                    message="No object at this key in storage.",
                )
            )

        async for event in _process(request, sources, missing):
            yield event
    finally:
        await index_registry.release(claimed)


async def _process(
    request: IndexRequest,
    sources: list[SourceObject],
    missing: list[str],
) -> AsyncIterator[object]:
    """Embed each selected file, yielding progress and a closing summary."""
    # Keys that were named but missing already count against the run.
    indexed = skipped = 0
    failed = len(missing)
    total_chunks = total_pruned = 0
    processed_keys: list[str] = []

    # --- Process each file -------------------------------------------------
    for position, source in enumerate(sources, start=1):
        result = IngestionResult(source.key)

        try:
            async for stage, result in index_source(
                source,
                request.chunk_size,
                request.chunk_overlap,
                request.embedding_model,
            ):
                yield IndexProgressEvent(
                    data=IndexProgressEventData(
                        source_key=source.key,
                        stage=stage,
                        file_number=position,
                        total_files=len(sources),
                        chunk_count=result.chunk_count,
                    )
                )
        except UnsupportedSourceType as exc:
            # Not a failure of the run — just a file this pipeline cannot read.
            skipped += 1
            processed_keys.append(source.key)
            yield IndexCompletedEvent(
                data=IndexCompletedEventData(
                    source_key=source.key,
                    skipped=True,
                    state=IndexState.UNSUPPORTED,
                )
            )
            yield IndexErrorEvent(
                data=IndexErrorEventData(
                    source_key=source.key, stage="extraction", message=str(exc)
                )
            )
            continue
        except Exception as exc:
            # One file failing must not end the run; report it and carry on.
            failed += 1
            yield IndexErrorEvent(
                data=IndexErrorEventData(
                    source_key=source.key, stage="indexing", message=str(exc)
                )
            )
            continue

        indexed += 1
        total_chunks += result.chunk_count
        total_pruned += result.pruned
        processed_keys.append(source.key)

        yield IndexCompletedEvent(
            data=IndexCompletedEventData(
                source_key=source.key,
                chunk_count=result.chunk_count,
                pruned=result.pruned,
                skipped=False,
                state=IndexState.CURRENT,
            )
        )

    # --- Close with the totals --------------------------------------------
    # Re-read each file's status from both sides rather than asserting it, so
    # the client's list refreshes from what is actually stored.
    statuses: list[SourceStatus] = list(
        await asyncio.gather(*(sync_status.get_status(key) for key in processed_keys))
    )

    yield IndexSummaryEvent(
        data=IndexSummaryEventData(
            indexed=indexed,
            skipped=skipped,
            failed=failed,
            total_chunks=total_chunks,
            total_pruned=total_pruned,
            statuses=statuses,
        )
    )


async def _describe(source_key: str) -> Optional[SourceObject]:
    """Fetch an object's metadata, or None when it no longer exists."""
    try:
        return await head_object(source_key)
    except FileNotFoundError:
        return None
