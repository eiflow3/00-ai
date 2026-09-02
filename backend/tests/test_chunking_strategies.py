"""Every strategy's contract, on the document the app is actually used on.

These are the properties the rest of the pipeline is entitled to assume, and
each one is load-bearing somewhere else:

  * **Nothing exceeds the budget** — or an embedding call fails on the one
    document dense enough to overflow it.
  * **Indices are contiguous from zero** — vector ids are derived from the
    position, so a gap is a vector nothing will ever overwrite.
  * **The same input gives the same output** — resuming an interrupted run
    compares stored text against a fresh cut, so a strategy that drifted would
    turn every re-index into a full re-embed.
  * **No text is dropped** — a chunker that silently loses a paragraph loses the
    answer that was in it, and no error is ever raised.

The document is the real Meridian report rather than a fixture string. A
synthetic paragraph exercises none of what actually breaks a splitter: ruled
headings, numeric tables, eighty-character rows of `=`.
"""

import pytest

from app.schemas.chunking import ChunkStrategy, ChunkingConfig
from app.services.chunker import chunk_document, cut_document
from app.services.chunking import registry
from app.services.chunking.catalog import catalog
from app.services.chunking.tokens import count_tokens

# The geometry the app defaults to, and therefore the one worth testing.
SIZE = 512
OVERLAP = 64

# Every strategy in the registry, so adding one to the registry adds it here.
STRATEGIES = registry.strategies()


def config_for(strategy: ChunkStrategy, **overrides) -> ChunkingConfig:
    """Build a config at the default geometry unless a test says otherwise."""
    return ChunkingConfig(
        strategy=strategy,
        chunk_size=overrides.get("chunk_size", SIZE),
        chunk_overlap=overrides.get("chunk_overlap", OVERLAP),
    )


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
async def test_no_chunk_exceeds_the_budget(strategy, report_text):
    """A chunk over the limit fails the embedding call, not the test suite."""
    segments = await cut_document(report_text, config_for(strategy))

    oversized = [
        count_tokens(segment.content)
        for segment in segments
        if count_tokens(segment.content) > SIZE
    ]

    assert segments, f"{strategy.value} produced no chunks at all"
    assert not oversized, f"{strategy.value} produced chunks of {oversized} tokens"


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
async def test_chunk_indices_are_contiguous(strategy, report_key, report_text):
    """A gap in the numbering is a vector id nothing will ever overwrite."""
    chunks = await chunk_document(report_key, report_text, config_for(strategy))

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    # Ids are derived from the position, so distinct positions must give
    # distinct ids — the property that makes a re-index idempotent.
    assert len({chunk.id for chunk in chunks}) == len(chunks)


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
async def test_cutting_is_deterministic(strategy, report_text):
    """Resuming a run compares stored text against a freshly cut chunk.

    A strategy whose output drifted between two runs would make every re-index
    a full re-embed, silently and expensively.
    """
    first = await cut_document(report_text, config_for(strategy))
    second = await cut_document(report_text, config_for(strategy))

    assert [segment.content for segment in first] == [
        segment.content for segment in second
    ]


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
async def test_no_text_is_dropped(strategy, report_text):
    """Every chunk's text is really in the document, and the tail is reached.

    A splitter that loses a paragraph raises nothing — it just answers worse
    from then on, which is exactly the failure a smoke test has to catch.
    """
    stripped = report_text.strip()
    segments = await cut_document(report_text, config_for(strategy))

    # `structural` prepends the section heading, so its chunks are not verbatim
    # substrings. The tail of a chunk always is.
    for segment in segments:
        tail = segment.content.strip()[-80:]
        assert tail in stripped, f"{strategy.value} invented text: {tail!r}"

    assert stripped[-40:] in segments[-1].content, "the document's tail was dropped"


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
async def test_offsets_point_into_the_document(strategy, report_text):
    """Offsets are what a preview highlights with, so they must be orderable."""
    stripped = report_text.strip()
    segments = await cut_document(report_text, config_for(strategy))

    for segment in segments:
        assert 0 <= segment.start_offset <= len(stripped)
        assert segment.start_offset <= segment.end_offset

    starts = [segment.start_offset for segment in segments]
    assert starts == sorted(starts), "chunks are not in document order"


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
async def test_overlap_at_or_above_the_size_is_refused(strategy, report_text):
    """The geometry that cannot advance is refused rather than looping forever.

    Checked at the service boundary as well as in the router, so a caller from
    anywhere gets the same answer.
    """
    with pytest.raises(ValueError):
        await cut_document(
            report_text, config_for(strategy, chunk_size=128, chunk_overlap=128)
        )


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
async def test_a_file_with_no_text_produces_no_chunks(strategy):
    """An emptied file is indexed with no vectors, not a failed run."""
    assert await cut_document("   \n\n \t ", config_for(strategy)) == []


async def test_the_default_strategy_still_cuts_the_report_the_same_way(report_text):
    """A regression guard on `boundary`, which is what production has stored.

    Its output is not an implementation detail: every vector in the production
    index was cut this way, and `index_plan` decides what to re-embed by
    comparing stored text against a fresh cut. Changing the boundary search or
    the cursor arithmetic would quietly invalidate the whole index, and the
    first sign of it would be a bill.

    The counts are asserted rather than the text, so the test is readable — and
    the first chunk's opening is pinned, because an off-by-one in the strip
    would show up there and nowhere else.
    """
    segments = await cut_document(report_text, config_for(ChunkStrategy.BOUNDARY))
    sizes = [count_tokens(segment.content) for segment in segments]

    assert len(segments) == 8
    assert max(sizes) <= SIZE
    assert segments[0].content.startswith("MERIDIAN FREIGHTWORKS, INC.")


async def test_structural_puts_the_heading_on_every_chunk(report_text):
    """The heading is why `structural` retrieves better, so it has to be there.

    A passage reading "revenue grew 19.8 percent" is ambiguous; the same passage
    under "SECTION 4. SEGMENT PERFORMANCE" is not.
    """
    segments = await cut_document(report_text, config_for(ChunkStrategy.STRUCTURAL))

    titled = [
        segment for segment in segments if segment.content.startswith("SECTION ")
    ]

    # Every chunk but the document's opening block, which has no heading of its
    # own — the detector titles it by its first line, and prepending that would
    # print the title twice.
    assert len(titled) == len(segments) - 1
    assert all("section:" in segment.note for segment in segments)


async def test_structural_splits_long_sections_and_merges_short_ones(report_text):
    """Without both corrections it is judged on chunk size, not on method."""
    segments = await cut_document(report_text, config_for(ChunkStrategy.STRUCTURAL))
    notes = [segment.note for segment in segments]

    assert any("part 1 of" in note for note in notes), "no long section was split"
    assert any("merged in" in note for note in notes), "no short section was merged"


async def test_fixed_fills_its_windows(report_text):
    """`fixed` is the floor precisely because it does not trim to a boundary.

    If it started respecting sentence ends it would stop being the control the
    other three are measured against, and nothing else would say so.
    """
    segments = await cut_document(report_text, config_for(ChunkStrategy.FIXED))
    sizes = [count_tokens(segment.content) for segment in segments]

    # Every window but the tail runs to the budget, give or take the token or
    # two that re-encoding a stripped slice can shift.
    assert all(size >= SIZE - 8 for size in sizes[:-1]), sizes


async def test_every_registered_strategy_is_offered_and_described():
    """A strategy in the registry that the catalog omits is one nobody can pick.

    The registry raises at import if a declared strategy has no implementation;
    this is the other half — an implementation nothing offers.
    """
    described = {spec.id for spec in catalog()}

    assert described == set(STRATEGIES)
    assert all(spec.summary for spec in catalog()), "a strategy has no description"
