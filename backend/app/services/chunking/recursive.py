"""Recursive strategy — the coarsest separator that still fits.

The splitter most production stacks reach for.  The document is broken on blank
lines; any piece still too long for the budget is broken again on single lines,
then on sentence ends, then on spaces, and only a piece that survives all four
is cut at the token limit.  The pieces are then packed back together up to the
budget, so a chunk is a whole number of paragraphs where paragraphs fit and a
whole number of sentences where they do not.

The difference from `boundary` is where the decision is made.  `boundary` takes
a window and asks "is there a break near the end of it?", so a document whose
paragraphs are longer than the search zone gets hard cuts.  This one never
takes a window at all: it descends until every piece fits, so a cut mid-sentence
only happens where a single sentence is longer than the whole chunk budget.

The cost is uneven chunks.  Packing whole paragraphs means a chunk ends as soon
as the next paragraph would not fit, which on a document of long sections leaves
chunks well short of their budget.
"""

from app.schemas.chunking import ChunkingConfig
from app.services.chunking.base import Segment, StrategyContext
from app.services.chunking.tokens import count_tokens, get_encoder

# Separators in descending order of how much structure they preserve.  Each
# carries the name a preview uses to say where a chunk ends.
SEPARATORS: tuple[tuple[str, str], ...] = (
    ("\n\n", "a blank line"),
    ("\n", "a line end"),
    (". ", "a sentence end"),
    (" ", "a word break"),
)

# What the deepest level is called: no separator left, so the piece is cut on
# the token count itself.
HARD_LEVEL = "the token limit"


def _hard_split(
    text: str, budget: int, encoding_name: str
) -> list[tuple[str, str]]:
    """Cut a piece that no separator could shorten, at the token limit."""
    encoder = get_encoder(encoding_name)
    tokens = encoder.encode(text)
    return [
        (encoder.decode(tokens[start : start + budget]), HARD_LEVEL)
        for start in range(0, len(tokens), budget)
    ]


def _explode(
    text: str, level: int, budget: int, encoding_name: str
) -> list[tuple[str, str]]:
    """Break text down until every piece fits the budget.

    Each piece keeps the separator it was split on, so concatenating every
    piece reproduces the input exactly — which is what lets offsets be counted
    rather than searched for.

    Args:
        text: The text to break down.
        level: Index into SEPARATORS to try next.
        budget: Maximum tokens a piece may hold.
        encoding_name: Which tiktoken encoding to measure against.

    Returns:
        One (piece, level name) pair per piece, in document order.
    """
    if not text:
        return []

    if count_tokens(text, encoding_name) <= budget:
        # Named for the separator that produced it, which is what a preview
        # reports as the reason the chunk ends where it does.
        name = SEPARATORS[level - 1][1] if level else HARD_LEVEL
        return [(text, name)]

    if level >= len(SEPARATORS):
        return _hard_split(text, budget, encoding_name)

    separator, _ = SEPARATORS[level]
    parts = text.split(separator)

    # A separator that does not occur tells us nothing; drop to the next one
    # rather than recursing on an unchanged string.
    if len(parts) == 1:
        return _explode(text, level + 1, budget, encoding_name)

    # Re-attach the separator to every part but the last, so the pieces still
    # join back into the original text.
    rejoined = [part + separator for part in parts[:-1]] + parts[-1:]

    pieces: list[tuple[str, str]] = []
    for part in rejoined:
        pieces.extend(_explode(part, level + 1, budget, encoding_name))
    return pieces


async def split(
    text: str, config: ChunkingConfig, context: StrategyContext
) -> list[Segment]:
    """Break the document down to fitting pieces, then pack them back up.

    Args:
        text: The full document text.
        config: The strategy's geometry.
        context: Which encoding measures a chunk.

    Returns:
        The segments, in document order.

    Raises:
        ValueError: If the overlap is not smaller than the chunk size.
    """
    if config.chunk_overlap >= config.chunk_size:
        raise ValueError(
            f"chunk_overlap ({config.chunk_overlap}) must be smaller than "
            f"chunk_size ({config.chunk_size}); otherwise chunking cannot advance."
        )

    stripped = text.strip()
    if not stripped:
        return []

    pieces = _explode(stripped, 0, config.chunk_size, context.encoding_name)
    if not pieces:
        return []

    # Offsets are counted rather than searched for: the pieces concatenate back
    # into the document, so each one's position is the sum of the lengths
    # before it. Exact even where the same paragraph appears twice.
    offsets: list[int] = []
    position = 0
    for piece, _ in pieces:
        offsets.append(position)
        position += len(piece)

    sizes = [count_tokens(piece, context.encoding_name) for piece, _ in pieces]

    segments: list[Segment] = []
    start_index = 0

    while start_index < len(pieces):
        end_index = start_index
        total = 0

        # Take pieces while they fit. The `end_index == start_index` case
        # admits one oversized piece, which cannot happen — _explode already
        # cut everything to the budget — but leaves the loop unable to stall.
        while end_index < len(pieces) and (
            end_index == start_index or total + sizes[end_index] <= config.chunk_size
        ):
            total += sizes[end_index]
            end_index += 1

        content = "".join(piece for piece, _ in pieces[start_index:end_index])
        start = offsets[start_index]

        # Trim the edges without lying about where the text sits.
        lead = len(content) - len(content.lstrip())
        content = content.strip()

        if content:
            segments.append(
                Segment(
                    content,
                    start + lead,
                    start + lead + len(content),
                    f"cut at {pieces[end_index - 1][1]}",
                )
            )

        if end_index >= len(pieces):
            break

        # Step back over the trailing pieces that fit within the overlap, so
        # the next chunk repeats them. Never back to `start_index` itself —
        # that would emit the same chunk forever.
        carried = 0
        next_index = end_index
        while next_index > start_index + 1 and carried + sizes[next_index - 1] <= config.chunk_overlap:
            next_index -= 1
            carried += sizes[next_index]

        start_index = next_index

    return segments
