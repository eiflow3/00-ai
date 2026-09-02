"""Chunker — turns a document's text into identified, embeddable chunks.

Two jobs, and only the second one is here.

*Cutting* belongs to app.services.chunking: one module per strategy, chosen by
name from its registry.  Nothing in that package knows a file exists.

*Identity* is this module.  A cut segment becomes a `Chunk` with the vector id
it will occupy, derived from the source key so the same file always produces
the same ids and a re-index overwrites in place instead of accumulating
duplicates.  That rule lives in app.services.provenance and is spelled out
nowhere else.

The split is here rather than merged because the two change for different
reasons: a new way of cutting text is a new strategy module, while a change to
how a chunk is named would invalidate every vector already stored.
"""

import logging

from app.schemas.chunk import Chunk
from app.schemas.chunking import ChunkingConfig
from app.services.chunking import boundary, registry
from app.services.chunking.base import Segment, StrategyContext
from app.services.chunking.tokens import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_ENCODING,
    count_tokens,
)
from app.services.provenance import document_id_for, vector_id_for

logger = logging.getLogger(__name__)

# Re-exported so callers that only need to measure or to name a default do not
# have to reach into the chunking package for it.
__all__ = [
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_ENCODING",
    "chunk_document",
    "count_tokens",
    "cut_document",
    "split_text",
]


def split_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    encoding_name: str = DEFAULT_ENCODING,
) -> list[tuple[str, int, int]]:
    """Split text with the default strategy, without giving the pieces ids.

    Deliberately fixed to the boundary strategy rather than taking one as an
    argument. Its only caller cuts a document for a person to read, not for the
    index, and a run's chosen strategy has no bearing on that — a golden set
    drafted from a document should not change shape because somebody indexed
    the file a different way that morning.

    Args:
        text: The full document text.
        chunk_size: Maximum tokens per chunk.
        chunk_overlap: Tokens repeated between consecutive chunks.
        encoding_name: Which tiktoken encoding to measure against.

    Returns:
        One tuple per chunk of (content, start_offset, end_offset), where the
        offsets are character positions in the stripped text.

    Raises:
        ValueError: If the overlap is not smaller than the chunk size.
    """
    return [
        (segment.content, segment.start_offset, segment.end_offset)
        for segment in boundary.cut(text, chunk_size, chunk_overlap, encoding_name)
    ]


async def cut_document(
    text: str,
    config: ChunkingConfig,
    encoding_name: str = DEFAULT_ENCODING,
    embedding_model: str = "",
) -> list[Segment]:
    """Run a strategy over a document's text, without giving the cuts ids.

    The half of chunking that a preview wants: what a strategy would do to this
    file, before anything has been named, stored or paid for. `chunk_document`
    is this plus identity.

    Args:
        text: The extracted text of the whole file.
        config: Which strategy to cut with, and at what size and overlap.
        encoding_name: Which tiktoken encoding to measure against.
        embedding_model: The model these chunks would be embedded with.

    Returns:
        The segments, in document order.

    Raises:
        UnknownStrategy: If the config names a strategy with no implementation.
        ValueError: If the overlap is not smaller than the chunk size.
    """
    split = registry.get(config.strategy)
    context = StrategyContext(
        encoding_name=encoding_name, embedding_model=embedding_model
    )
    return await split(text, config, context)


async def chunk_document(
    source_key: str,
    text: str,
    config: ChunkingConfig,
    encoding_name: str = DEFAULT_ENCODING,
    embedding_model: str = "",
) -> list[Chunk]:
    """Split a source file's text into Chunk records ready for embedding.

    Args:
        source_key: The object key the text came from.
        text: The extracted text of the whole file.
        config: Which strategy to cut with, and at what size and overlap.
        encoding_name: Which tiktoken encoding to measure against.
        embedding_model: The model these chunks will be embedded with. Passed
            to the strategy because one that cuts by meaning has to measure in
            the same embedding space the chunks will later be searched in.

    Returns:
        Chunks in document order, each carrying its position and offsets.

    Raises:
        UnknownStrategy: If the config names a strategy with no implementation.
        ValueError: If the overlap is not smaller than the chunk size.
    """
    segments: list[Segment] = await cut_document(
        text, config, encoding_name, embedding_model
    )

    document_id = document_id_for(source_key)

    logger.debug(
        "%s: %s produced %d chunk(s) at %d/%d",
        source_key,
        config.strategy.value,
        len(segments),
        config.chunk_size,
        config.chunk_overlap,
    )

    return [
        Chunk(
            # The chunk id *is* the vector id — one identity across both, so a
            # retrieved chunk can be traced straight back to its source file.
            id=vector_id_for(document_id, index),
            document_id=document_id,
            content=segment.content,
            chunk_index=index,
            overlap=config.chunk_overlap if index else 0,
            start_offset=segment.start_offset,
            end_offset=segment.end_offset,
            char_count=len(segment.content),
        )
        for index, segment in enumerate(segments)
    ]
