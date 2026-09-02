"""Maps a retrieved chunk back to the section of the document it came from.

Scoring retrieval needs the two sides to speak the same language.  A golden row
says the answer lives in "SECTION 3. FINANCIAL HIGHLIGHTS"; retrieval returns a
passage of text.  Something has to say which section that passage is in, and it
cannot be the chunk's metadata: the whole point of the comparison is that four
strategies cut the same document four ways, and only one of them knows about
headings at all.

So the mapping is computed from the document itself, at scoring time.  Sections
are located once, chunks are located within them, and every strategy is judged
by the same yardstick.

Two properties make this exact rather than approximate:

  * **A chunk is located by probes, not by its whole text.**  The structural
    strategy prepends a heading that does not appear at that point in the file,
    so searching for the chunk verbatim would fail on exactly the strategy most
    likely to win.
  * **A chunk can be in more than one section.**  A cut that straddles a
    heading covers both, and reporting only the first would understate the
    recall of every strategy that ignores headings.
"""

import logging

from app.services.document_sections import split_sections

logger = logging.getLogger(__name__)

# Characters per probe.  Long enough to be unique in a document, short enough
# that a probe taken from the middle of a chunk rarely crosses its end.
PROBE_LENGTH = 96

# Where in a chunk the probes are taken from, as fractions of its length.  The
# tail is first because it is the one place no strategy inserts anything.
PROBE_POSITIONS = (1.0, 0.5, 0.0)


def section_spans(text: str) -> list[tuple[str, int, int]]:
    """Locate every section of a document by character offset.

    Args:
        text: The document's full text.

    Returns:
        One (title, start, end) per section with text under it, in document
        order. Offsets are into the stripped text, which is what every chunking
        strategy is handed.
    """
    stripped = text.strip()
    spans: list[tuple[str, int, int]] = []
    cursor = 0

    for section in split_sections(stripped):
        body = section.body.strip()
        if not body:
            continue

        start = stripped.find(body, cursor)
        if start == -1:
            start = stripped.find(body)
        if start == -1:
            # A body the detector reshaped rather than quoted. Skipping it is
            # better than pinning it to the wrong offset, which would move
            # every chunk after it into the wrong section.
            logger.debug("Section %r could not be located in the text", section.title)
            continue

        cursor = start
        spans.append((section.title, start, start + len(body)))

    return spans


def _probes(content: str) -> list[str]:
    """Take short samples from a chunk, for locating it in the document."""
    stripped = content.strip()
    if not stripped:
        return []

    samples: list[str] = []
    for fraction in PROBE_POSITIONS:
        end = max(PROBE_LENGTH, int(len(stripped) * fraction))
        sample = stripped[max(0, end - PROBE_LENGTH) : end].strip()
        if len(sample) >= 16 and sample not in samples:
            samples.append(sample)

    return samples


def sections_for(
    content: str, text: str, spans: list[tuple[str, int, int]]
) -> list[str]:
    """Name the sections a retrieved chunk's text falls in.

    Args:
        content: The chunk's text, as retrieval returned it.
        text: The document it came from.
        spans: Section spans from `section_spans`, computed once per document.

    Returns:
        The titles the chunk covers, in document order. Empty when the chunk
        cannot be located — a chunk from a different file, or text the
        extractor no longer produces.
    """
    stripped = text.strip()

    found: list[str] = []
    for probe in _probes(content):
        position = stripped.find(probe)
        if position == -1:
            continue
        for title, start, end in spans:
            if start <= position < end and title not in found:
                found.append(title)

    return found
