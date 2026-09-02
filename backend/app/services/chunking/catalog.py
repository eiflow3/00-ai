"""What each strategy is, written for the person choosing between them.

Kept apart from the registry for the same reason `services.llm.catalog` is kept
apart from its factory: one answers "what can this code do", the other answers
"what should I pick, and what will it cost me".  A client renders its picker
from here rather than hardcoding a list that drifts out of date.

Every description is behavioural — what the strategy does to a document and
where it tends to win or lose.  None of them mention tokens, windows or
separators, because that is not what a person is choosing between.
"""

from app.schemas.chunking import ChunkStrategy, ChunkStrategySpec
from app.services.chunking.registry import strategies

_SPECS: dict[ChunkStrategy, ChunkStrategySpec] = {
    ChunkStrategy.BOUNDARY: ChunkStrategySpec(
        id=ChunkStrategy.BOUNDARY,
        label="Default",
        summary="Cuts every so many tokens, backing up to the nearest blank line "
        "or sentence end if one is close.",
        detail="What the pipeline has always done, and the baseline the others "
        "are measured against. Reliable on prose. On a document whose "
        "paragraphs are longer than the search window it gives up and cuts "
        "mid-sentence, which is where the other strategies get their chance.",
    ),
    ChunkStrategy.FIXED: ChunkStrategySpec(
        id=ChunkStrategy.FIXED,
        label="Fixed",
        summary="Cuts every so many tokens exactly, wherever that lands — "
        "mid-sentence, mid-word, mid-number.",
        detail="The floor. Every cleverer strategy claims to beat it, and until "
        "you have run it on your own document that claim is untested. It does "
        "have one real advantage: every chunk is the same size, so no match is "
        "flattered by simply being longer than its rivals.",
    ),
    ChunkStrategy.RECURSIVE: ChunkStrategySpec(
        id=ChunkStrategy.RECURSIVE,
        label="Recursive",
        summary="Splits on blank lines first, then lines, then sentences, then "
        "spaces — going finer only where a piece is still too long.",
        detail="What most production splitters do. A chunk is a whole number of "
        "paragraphs wherever paragraphs fit, so a sentence is only broken when "
        "that one sentence is longer than the whole budget. The cost is uneven "
        "chunks: a chunk ends as soon as the next paragraph would not fit.",
    ),
    ChunkStrategy.STRUCTURAL: ChunkStrategySpec(
        id=ChunkStrategy.STRUCTURAL,
        label="Structural",
        summary="Uses the document's own headings, one section to a chunk, and "
        "puts the heading at the top of every chunk it produces.",
        detail="Strongest on a document that is genuinely sectioned — a report, "
        "a contract, a policy — because a section is already sized by the "
        "argument being made. Sections too long are split inside themselves and "
        "sections too short are merged forward, so it is held to the same chunk "
        "size as everything else. On a document with no headings it has nothing "
        "to work with and comes out close to the default.",
        # Overlap applies only inside a section long enough to be split: a
        # heading is a real boundary, and repeating text across one buys
        # nothing that the heading on the next chunk does not already give.
        honours_overlap=True,
    ),
}


def catalog() -> list[ChunkStrategySpec]:
    """Describe every strategy on offer, in the order to present them.

    Returns:
        One spec per registered strategy. Driven by the registry rather than by
        this module's own dict, so a strategy can never be implemented and then
        quietly left out of the picker.
    """
    return [_SPECS[strategy] for strategy in strategies() if strategy in _SPECS]


def spec(strategy: ChunkStrategy) -> ChunkStrategySpec:
    """Describe one strategy.

    Args:
        strategy: Which strategy to describe.

    Returns:
        Its spec, or a bare one naming it when no description was written —
        a missing description should not stop a working strategy being used.
    """
    return _SPECS.get(
        strategy,
        ChunkStrategySpec(id=strategy, label=strategy.value.title(), summary=""),
    )
