"""Real Docling against the edge-case fixture — the one test a fake cannot do.

The offline suite fakes extraction at the seam, which proves everything except
the extractor itself.  This runs the real thing over
`fixtures/edge_cases.pdf`, whose ground truth is written down beside it in
`edge_cases.md`: six pages, a running header on all of them, two tables, a
two-column page, a page whose text exists only as pixels, and a sentence that
crosses a page break.

Marked live because the first run downloads Docling's model weights (hundreds
of megabytes) and a conversion takes real seconds — not because it spends
money.  It spends none: Docling runs locally.
"""

from pathlib import Path

import pytest

from app.services import pdf_extraction

FIXTURE = Path(__file__).parent / "fixtures" / "edge_cases.pdf"

pytestmark = pytest.mark.live


def test_docling_reads_the_edge_case_fixture():
    result = pdf_extraction.extract_pdf(FIXTURE.read_bytes(), "edge_cases.pdf")
    text = result.text

    # Six pages, spans contiguous over the final text.
    assert [span.page for span in result.pages] == [1, 2, 3, 4, 5, 6]
    assert result.pages[0].start_offset == 0
    assert result.pages[-1].end_offset == len(text)
    for before, after in zip(result.pages, result.pages[1:]):
        assert before.end_offset == after.start_offset

    # Visible page markers in the stored markdown.
    assert "<!-- page 2 -->" in text

    # The tables page yielded structured tables, not word soup.
    assert len(result.tables) >= 2
    assert any("503.2" in table.markdown for table in result.tables)

    # Page 4's body exists only as pixels — this word reaching the text at all
    # means OCR ran. "tranches" appears nowhere else in the document.
    assert "tranches" in text.lower()

    # The sentence that crosses the page 5 -> 6 break survived in order.
    assert "planning threshold" in text
    assert "four quarters" in text
