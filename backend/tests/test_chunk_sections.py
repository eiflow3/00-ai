"""Mapping a retrieved chunk back to the section it came from.

This is the quiet load-bearing piece of the whole comparison. A golden row cites
"SECTION 3. FINANCIAL HIGHLIGHTS"; retrieval returns a passage of text; if
nothing can say which section that passage is in, every variant scores zero
recall and the ranking is noise.

It has to work for all four strategies equally, and that is not free:

  * `structural` prepends a heading that does not appear at that point in the
    file, so searching for a chunk verbatim fails on exactly the strategy most
    likely to win;
  * the others cut across headings, so a chunk can belong to two sections and
    reporting one would understate their recall.

Both are asserted below, because a regression in either would not raise — it
would just quietly decide the experiment.
"""

import pytest

from app.schemas.chunking import ChunkStrategy, ChunkingConfig
from app.services.chunk_sections import section_spans, sections_for
from app.services.chunker import cut_document
from app.services.chunking import registry
from app.services.document_sections import split_sections

STRATEGIES = registry.strategies()


def test_the_report_s_sections_are_found_and_ordered(report_text):
    """The spans are the yardstick, so they have to match the outline."""
    spans = section_spans(report_text)
    titles = [title for title, _, _ in spans]

    assert "SECTION 3. FINANCIAL HIGHLIGHTS" in titles
    assert "SECTION 4. SEGMENT PERFORMANCE" in titles

    # Same titles the golden-set generator cites, since both read the outline
    # from `document_sections` — if they diverged, every gold section would be
    # a string retrieval could never report.
    outline = {section.title for section in split_sections(report_text.strip())}
    assert set(titles) <= outline

    starts = [start for _, start, _ in spans]
    assert starts == sorted(starts)
    assert all(start < end for _, start, end in spans)


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
async def test_every_chunk_maps_to_at_least_one_section(strategy, report_text):
    """An unmapped chunk is a chunk that can never count towards recall.

    Which would read as the strategy retrieving the wrong thing, when in fact
    the scorer simply could not tell where its text came from.
    """
    spans = section_spans(report_text)
    segments = await cut_document(
        report_text, ChunkingConfig(strategy=strategy, chunk_size=512, chunk_overlap=64)
    )

    unmapped = [
        segment.content[:60]
        for segment in segments
        if not sections_for(segment.content, report_text, spans)
    ]

    assert not unmapped, f"{strategy.value} produced chunks nothing could place"


async def test_a_structural_chunk_maps_despite_its_injected_heading(report_text):
    """The case that breaks a naive `text.find(chunk)` implementation."""
    spans = section_spans(report_text)
    segments = await cut_document(
        report_text,
        ChunkingConfig(strategy=ChunkStrategy.STRUCTURAL, chunk_size=512, chunk_overlap=64),
    )

    financials = next(
        segment
        for segment in segments
        if segment.content.startswith("SECTION 3. FINANCIAL HIGHLIGHTS")
    )

    # The chunk is not a substring of the document — the heading was added —
    # so this only passes because the mapping probes rather than searches whole.
    assert financials.content not in report_text
    assert "SECTION 3. FINANCIAL HIGHLIGHTS" in sections_for(
        financials.content, report_text, spans
    )


async def test_a_chunk_that_straddles_a_heading_reports_both_sections(report_text):
    """Reporting only the first would understate every unstructured strategy."""
    spans = section_spans(report_text)
    segments = await cut_document(
        report_text,
        ChunkingConfig(strategy=ChunkStrategy.FIXED, chunk_size=512, chunk_overlap=64),
    )

    spanning = [
        sections_for(segment.content, report_text, spans) for segment in segments
    ]

    assert any(len(found) > 1 for found in spanning), (
        "no fixed-size chunk crossed a heading, which cannot be true of this "
        "document — the mapping is probably reporting only one section"
    )


def test_text_from_another_document_maps_to_nothing(report_text):
    """A chunk the document does not contain is unplaceable, not misplaced."""
    spans = section_spans(report_text)

    assert sections_for("a passage from some other file entirely", report_text, spans) == []
