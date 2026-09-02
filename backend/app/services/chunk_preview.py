"""Preview — how a strategy would cut a file, before anything is embedded.

The cheap half of the experiment.  Reading a file and running a splitter over
it costs nothing but a moment; embedding the result costs money and, once
several variants exist, patience.  So the choice of strategy is made against a
preview, and only a strategy that looks worth trying gets indexed.

What a preview is really for is the summary, not the chunk list.  Two
strategies at the same nominal size can produce twenty-four chunks and eleven,
and that difference decides more about retrieval than anything visible in the
text of any one chunk.
"""

import logging
import statistics

from app.schemas.chunking import (
    ChunkingConfig,
    ChunkPreviewResponse,
    ChunkPreviewStats,
    PreviewChunk,
)
from app.services import chunk_variants
from app.services.chunker import cut_document
from app.services.chunking.tokens import count_tokens
from app.services.object_store import get_object
from app.services.text_extraction import extract_text

logger = logging.getLogger(__name__)


def _stats(sizes: list[int], document_tokens: int) -> ChunkPreviewStats:
    """Summarise a cut in the six numbers worth comparing.

    Args:
        sizes: Token count of each chunk, in document order.
        document_tokens: Tokens in the document itself.

    Returns:
        The summary. Everything is zero for a document that produced no chunks,
        which is a real outcome rather than an error.
    """
    if not sizes:
        return ChunkPreviewStats(document_tokens=document_tokens)

    total = sum(sizes)

    return ChunkPreviewStats(
        chunk_count=len(sizes),
        total_tokens=total,
        document_tokens=document_tokens,
        min_tokens=min(sizes),
        median_tokens=int(statistics.median(sizes)),
        max_tokens=max(sizes),
        # Everything embedded beyond the document's own length is a repeat of a
        # neighbouring chunk: what overlap costs, stated as a share so it can be
        # compared between strategies that produce different numbers of chunks.
        repeated_fraction=max(0.0, 1 - document_tokens / total) if total else 0.0,
    )


async def preview(source_key: str, config: ChunkingConfig) -> ChunkPreviewResponse:
    """Cut a stored file with a strategy and describe the result.

    Nothing is embedded, nothing is written, and no vector is touched.

    Args:
        source_key: The object key to read.
        config: Which strategy to apply, and at what size and overlap.

    Returns:
        Every chunk the strategy produces, with the shape of the cut.

    Raises:
        FileNotFoundError: If no object exists at that key.
        UnsupportedSourceType: If no extractor handles this file type.
        UnknownStrategy: If the config names a strategy with no implementation.
        ValueError: If the overlap is not smaller than the chunk size.
    """
    data = await get_object(source_key)
    text = extract_text(source_key, data)

    segments = await cut_document(text, config)

    chunks = [
        PreviewChunk(
            chunk_index=index,
            content=segment.content,
            token_count=count_tokens(segment.content),
            char_count=len(segment.content),
            start_offset=segment.start_offset,
            end_offset=segment.end_offset,
            note=segment.note,
        )
        for index, segment in enumerate(segments)
    ]

    logger.info(
        "%s previewed with %s: %d chunk(s)",
        source_key,
        config.strategy.value,
        len(chunks),
    )

    return ChunkPreviewResponse(
        source_key=source_key,
        # Named even though nothing was written, so the client can say exactly
        # which variant pressing Index would create.
        variant_id=chunk_variants.variant_id(config),
        label=chunk_variants.label_for(config),
        config=config,
        stats=_stats([chunk.token_count for chunk in chunks], count_tokens(text.strip())),
        chunks=chunks,
    )
