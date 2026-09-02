"""Fixed strategy — equal token windows, cut wherever the count runs out.

No boundary search: a chunk ends when the budget does, mid-sentence and
mid-word alike.  This is the honest floor.  Every cleverer strategy claims to
beat it, and until you have run it on your own document that claim is untested
— which is exactly why it is worth having in the list rather than assumed.

What it costs is context at the edges: a figure separated from the row label
that names it, a pronoun separated from its subject.  What it buys is chunks of
genuinely uniform size, so similarity scores are never distorted by one chunk
being three times longer than another.
"""

from app.schemas.chunking import ChunkingConfig
from app.services.chunking.base import Segment, StrategyContext, locate
from app.services.chunking.tokens import get_encoder

# What a preview says about each cut.
NOTE_WINDOW = "fixed window, cut at the token limit"
NOTE_TAIL = "the document's tail, shorter than a full window"


async def split(
    text: str, config: ChunkingConfig, context: StrategyContext
) -> list[Segment]:
    """Cut a document into equal token windows.

    Args:
        text: The full document text.
        config: The strategy's geometry.
        context: Which encoding measures a chunk.

    Returns:
        The segments, in document order.

    Raises:
        ValueError: If the overlap is not smaller than the chunk size, which
            would make the splitter loop forever.
    """
    if config.chunk_overlap >= config.chunk_size:
        raise ValueError(
            f"chunk_overlap ({config.chunk_overlap}) must be smaller than "
            f"chunk_size ({config.chunk_size}); otherwise chunking cannot advance."
        )

    stripped = text.strip()
    if not stripped:
        return []

    encoder = get_encoder(context.encoding_name)
    tokens = encoder.encode(stripped)

    step = max(1, config.chunk_size - config.chunk_overlap)

    segments: list[Segment] = []
    cursor = 0
    token_cursor = 0

    while token_cursor < len(tokens):
        window = tokens[token_cursor : token_cursor + config.chunk_size]
        is_last = token_cursor + config.chunk_size >= len(tokens)

        # Stripped only of the whitespace at its edges: a chunk that opens with
        # half a newline is the same chunk, and the schema will not store an
        # empty one. Where the window falls is untouched.
        content = encoder.decode(window).strip()

        if content:
            start, end = locate(stripped, content, cursor)
            segments.append(
                Segment(content, start, end, NOTE_TAIL if is_last else NOTE_WINDOW)
            )
            cursor = start

        if is_last:
            break

        token_cursor += step

    return segments
