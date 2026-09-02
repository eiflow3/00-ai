"""Boundary strategy — a fixed token window, trimmed back to a natural break.

The pipeline's original chunker, and the baseline every other strategy is
measured against.  A window of `chunk_size` tokens is taken, then trimmed back
to the last paragraph or sentence break in its final quarter; if no break falls
that late the window is taken whole, because a chunk shrunken to a third of its
budget costs more in specificity than the ragged edge costs in meaning.

Two rules carry the design:

  * **The last qualifying break wins**, not the first.  Searching forward and
    taking the first break would truncate a 512-token window back to its
    opening paragraph.
  * **The step is measured against what was emitted**, not against the nominal
    window.  When the boundary search trims 512 tokens down to 400, the cursor
    advances 336, so the next chunk still begins exactly `chunk_overlap` tokens
    before this one ended.  Stepping by `chunk_size - chunk_overlap` instead
    would open a gap of unembedded text at every trimmed boundary.
"""

import re

from app.schemas.chunking import ChunkingConfig
from app.services.chunking.base import Segment, StrategyContext
from app.services.chunking.tokens import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_ENCODING,
    get_encoder,
)

# How far back from a hard cut we will look for a natural boundary, as a
# fraction of the chunk.  Beyond this the boundary is too far back to be worth
# the shrunken chunk, so we take the hard cut instead.
BOUNDARY_SEARCH_FRACTION = 0.25

# Paragraph break: a blank line, however much trailing whitespace it carries.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

# Sentence end: terminal punctuation followed by whitespace.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s")

# What a preview says about how each cut was chosen.
NOTE_PARAGRAPH = "cut at a blank line"
NOTE_SENTENCE = "cut at a sentence end"
NOTE_HARD = "no break in reach — cut at the token limit"
NOTE_TAIL = "the document's tail, taken whole"
NOTE_WHOLE = "the whole document fits in one chunk"


def _find_boundary(text: str, earliest: int) -> tuple[int, str]:
    """Find the best natural break in `text` at or after `earliest`.

    Prefers the last paragraph break, falling back to the last sentence end.
    Paragraph beats sentence unconditionally: if any blank line falls in the
    search zone, no sentence break is considered — even one sitting later that
    would have produced a fuller chunk.

    Args:
        text: The candidate chunk's text.
        earliest: Character offset before which a break is not worth taking.

    Returns:
        The character offset to cut at and why, or `(0, NOTE_HARD)` when there
        is no usable break.
    """
    for pattern, note in ((_PARAGRAPH_BREAK, NOTE_PARAGRAPH), (_SENTENCE_BREAK, NOTE_SENTENCE)):
        # Take the *last* match, so the chunk stays as full as possible.
        matches = [m for m in pattern.finditer(text) if m.end() >= earliest]
        if matches:
            return matches[-1].end(), note
    return 0, NOTE_HARD


def cut(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    encoding_name: str = DEFAULT_ENCODING,
) -> list[Segment]:
    """Split text into overlapping segments sized by token count.

    Args:
        text: The full document text.
        chunk_size: Maximum tokens per chunk.
        chunk_overlap: Tokens repeated between consecutive chunks.
        encoding_name: Which tiktoken encoding to measure against.

    Returns:
        The segments, in document order.

    Raises:
        ValueError: If the overlap is not smaller than the chunk size, which
            would make the splitter loop forever.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) must be smaller than "
            f"chunk_size ({chunk_size}); otherwise chunking cannot advance."
        )

    stripped = text.strip()
    if not stripped:
        return []

    encoder = get_encoder(encoding_name)
    tokens = encoder.encode(stripped)

    # Text short enough to embed whole needs no splitting at all.
    if len(tokens) <= chunk_size:
        return [Segment(stripped, 0, len(stripped), NOTE_WHOLE)]

    segments: list[Segment] = []
    # Character offset in `stripped` where the next chunk begins.
    cursor = 0
    # Token offset, tracked separately so overlap is measured in tokens.
    token_cursor = 0

    while token_cursor < len(tokens):
        window = tokens[token_cursor : token_cursor + chunk_size]
        candidate = encoder.decode(window)

        # The final window runs to the end of the text; take it as-is rather
        # than trimming it back to a boundary and dropping the remainder.
        is_last = token_cursor + chunk_size >= len(tokens)
        if is_last:
            content, note = candidate, NOTE_TAIL
        else:
            earliest = int(len(candidate) * (1 - BOUNDARY_SEARCH_FRACTION))
            boundary, note = _find_boundary(candidate, earliest)
            content = candidate[:boundary] if boundary else candidate

        content = content.strip()
        if content:
            # Offsets describe where this chunk sits in the original text, so
            # a caller can highlight the passage in the source file.
            start = stripped.find(content, cursor)
            start = start if start != -1 else cursor
            segments.append(Segment(content, start, start + len(content), note))

        if is_last:
            break

        # Advance by the consumed text minus the overlap.  Measuring the step
        # in tokens keeps the overlap exact even when the boundary search
        # trimmed the chunk well short of chunk_size.
        consumed = len(encoder.encode(content)) or chunk_size
        step = max(1, consumed - chunk_overlap)
        token_cursor += step
        cursor = len(encoder.decode(tokens[:token_cursor]))

    return segments


async def split(
    text: str, config: ChunkingConfig, context: StrategyContext
) -> list[Segment]:
    """Cut a document at natural breaks near the token budget."""
    return cut(text, config.chunk_size, config.chunk_overlap, context.encoding_name)
