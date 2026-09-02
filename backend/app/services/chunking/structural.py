"""Structural strategy — the document's own headings decide the cuts.

Where an author marked the structure, that structure is already sized by the
argument being made, which is usually the unit a question gets asked about.  A
section is a chunk.

Two corrections keep that from being naive, and without them this strategy
would be judged on chunk size rather than on method:

  * **Sections longer than the budget are split inside themselves**, at natural
    breaks, so no chunk exceeds the limit every other strategy is held to.
  * **Sections too short to stand alone are merged forward** into the next one.
    A three-line preamble embedded on its own is a vector that matches
    everything weakly and nothing well.

Each chunk carries its heading at the top.  A retrieved passage that says
"revenue grew 19.8 percent" is ambiguous; the same passage under "4.2 Cold
Chain" is not, and the heading costs a dozen tokens to keep.

The heading detection is not this module's: `services.document_sections`
already reads ruled headings, ATX headings and numbered or capitalised lines,
because the golden-set generator needed exactly the same thing.  A second
implementation would be the same job done twice, and the two could disagree
about the same document.
"""

from dataclasses import dataclass

from app.schemas.chunking import ChunkingConfig
from app.services.chunking import boundary
from app.services.chunking.base import Segment, StrategyContext, locate
from app.services.chunking.tokens import count_tokens
from app.services.document_sections import split_sections

# Below this share of the chunk budget a section is not worth embedding alone,
# and is merged into the one that follows it.  A quarter is the same threshold
# the boundary search uses to decide a chunk is too shrunken to be worth its
# natural edge.
MIN_SECTION_FRACTION = 0.25

# Between a chunk's heading and the text beneath it.
HEADER_SEPARATOR = "\n\n"


def _titled(header: str, body: str) -> str:
    """Put the section's heading on a chunk, unless the text already carries it.

    A document's opening block has no heading of its own, so the detector
    titles it by its first line — and prepending that would print the title
    twice. Checking is cheaper than special-casing the preamble.
    """
    if not header or body.lstrip().startswith(header):
        return body
    return f"{header}{HEADER_SEPARATOR}{body}"


@dataclass
class _Span:
    """One section's title and where its text sits in the document."""

    title: str
    start: int
    end: int
    # How many sections were folded together to reach a usable size.
    merged: int = 1


def _spans(stripped: str) -> list[_Span]:
    """Locate every section's body in the document text.

    Args:
        stripped: The document text, already stripped.

    Returns:
        One span per section that has text under it, in document order. A
        heading with nothing beneath it is a title rather than a section and is
        left out.
    """
    spans: list[_Span] = []
    cursor = 0

    for section in split_sections(stripped):
        body = section.body.strip()
        if not body:
            continue

        start, end = locate(stripped, body, cursor)
        cursor = start
        spans.append(_Span(section.title, start, end))

    return spans


def _merge_runts(
    spans: list[_Span], stripped: str, minimum: int, encoding_name: str
) -> list[_Span]:
    """Fold sections too small to stand alone into the section after them.

    Merging forward rather than backward keeps a preamble attached to what it
    introduces, which is the direction the text itself reads.

    Args:
        spans: Sections in document order.
        stripped: The document text, used to measure a span.
        minimum: Fewest tokens a section may hold and still stand alone.
        encoding_name: Which tiktoken encoding to measure against.

    Returns:
        The spans, with runs of small sections combined. A merged span covers
        the source text between its first and last section, so the headings
        inside it survive verbatim rather than being dropped or re-inserted.
    """
    merged: list[_Span] = []
    pending: list[_Span] = []

    for span in spans:
        pending.append(span)
        covered = stripped[pending[0].start : span.end]

        if count_tokens(covered, encoding_name) >= minimum:
            merged.append(
                _Span(pending[0].title, pending[0].start, span.end, len(pending))
            )
            pending = []

    # A tail that never reached the minimum joins the section before it rather
    # than being emitted as the runt this whole function exists to avoid.
    if pending:
        if merged:
            last = merged[-1]
            merged[-1] = _Span(
                last.title, last.start, pending[-1].end, last.merged + len(pending)
            )
        else:
            merged.append(
                _Span(pending[0].title, pending[0].start, pending[-1].end, len(pending))
            )

    return merged


def _note(span: _Span, part: int = 0, parts: int = 0) -> str:
    """Describe a cut for a preview: its heading, and how it was adjusted."""
    note = f"section: {span.title}"
    if span.merged > 1:
        note += f" (+{span.merged - 1} short section(s) merged in)"
    if parts > 1:
        note += f" — part {part} of {parts}"
    return note


async def split(
    text: str, config: ChunkingConfig, context: StrategyContext
) -> list[Segment]:
    """Cut a document on its own headings, one section to a chunk.

    Args:
        text: The full document text.
        config: The strategy's geometry. `chunk_size` bounds a section as it
            bounds every other strategy's chunk; `chunk_overlap` applies only
            inside a section long enough to be split, since a heading is a real
            boundary and repeating text across one buys nothing.
        context: Which encoding measures a chunk.

    Returns:
        The segments, in document order.
    """
    stripped = text.strip()
    if not stripped:
        return []

    spans = _spans(stripped)
    if not spans:
        return []

    minimum = max(1, int(config.chunk_size * MIN_SECTION_FRACTION))
    spans = _merge_runts(spans, stripped, minimum, context.encoding_name)

    segments: list[Segment] = []

    for span in spans:
        header = span.title.strip()
        body = stripped[span.start : span.end]

        # The heading rides on every chunk of the section, so the budget for
        # the text itself is what is left after it.
        header_cost = count_tokens(header + HEADER_SEPARATOR, context.encoding_name)
        budget = max(1, config.chunk_size - header_cost)

        if count_tokens(body, context.encoding_name) <= budget:
            segments.append(
                Segment(_titled(header, body), span.start, span.end, _note(span))
            )
            continue

        # Too long to embed whole: split it at natural breaks, and repeat the
        # heading on each piece so no part of the section loses its name.
        pieces = boundary.cut(
            body, budget, config.chunk_overlap, context.encoding_name
        )

        for position, piece in enumerate(pieces, start=1):
            segments.append(
                Segment(
                    _titled(header, piece.content),
                    span.start + piece.start_offset,
                    span.start + piece.end_offset,
                    _note(span, position, len(pieces)),
                )
            )

    return segments
