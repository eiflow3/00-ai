"""What every chunking strategy is, and the few things they all share.

A strategy is one function: given a document's text and a geometry, return the
segments it would embed.  It never sees a file, an object key, a vector id or a
request — identity is attached afterwards by app.services.chunker, and storage
by app.services.ingestion.  Keeping strategies at that altitude is what makes
them comparable: two of them handed the same string differ only in where they
cut it.

Every strategy is `async` whether or not it awaits anything.  Cutting by
embedding distance or by asking a model for a summary is a real strategy people
will want next, and a synchronous protocol would have to be rewritten (and
every call site with it) the first time one arrives.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable

from app.schemas.chunking import ChunkingConfig
from app.services.chunking.tokens import DEFAULT_ENCODING


@dataclass(frozen=True)
class Segment:
    """One cut of the document, before it is given an identity.

    Offsets are character positions in the *stripped* document text — the same
    text the strategy was handed. They exist so a preview can show where a
    chunk came from; nothing downstream stores them.
    """

    # The text that will be embedded.
    content: str

    # Where this text sits in the document handed to the strategy.
    start_offset: int
    end_offset: int

    # Why the cut landed here, in a person's words: the heading a section chunk
    # sits under, or that no boundary was within reach. Shown in a preview and
    # nowhere else — it is diagnostic, not data.
    note: str = ""


@dataclass(frozen=True)
class StrategyContext:
    """Everything a strategy may need that is not the text or the geometry.

    Passed to every strategy even though today's four only read the encoding,
    so that a strategy needing to embed or to call a model can be added without
    changing the protocol and every call site with it.
    """

    # Which tiktoken encoding measures a chunk.
    encoding_name: str = DEFAULT_ENCODING

    # The model the resulting chunks will be embedded with. A strategy that
    # measures semantic distance needs to use this same model, or it would cut
    # according to one embedding space and be searched in another.
    embedding_model: str = ""


# What every strategy module exposes as `split`.
SplitFunction = Callable[[str, ChunkingConfig, StrategyContext], Awaitable[list[Segment]]]


class UnknownStrategy(ValueError):
    """Raised when a caller names a strategy with no implementation behind it."""


def locate(text: str, content: str, cursor: int) -> tuple[int, int]:
    """Find where a chunk's text sits in the document, searching from `cursor`.

    Searching forward from the previous chunk's position rather than from zero
    is what stops a passage repeated verbatim elsewhere in the document from
    anchoring this chunk to the wrong copy.

    Args:
        text: The document text the chunk came from.
        content: The chunk's text.
        cursor: Character position to search from.

    Returns:
        The chunk's start and end offsets. A search that fails falls back to
        the cursor rather than raising — an approximate offset is a degraded
        preview highlight, an exception is a failed ingestion.
    """
    start = text.find(content, cursor)
    if start == -1:
        start = text.find(content)
    if start == -1:
        start = cursor
    return start, start + len(content)
