"""Splits a document into the titled sections a golden row cites.

The golden set's `gold_sections` field is what separates "retrieved the wrong
chunk" from "retrieved the right chunk and answered badly", and it only works
if the titles written into a row are the same strings the retriever reports.
So headings are carried verbatim — never title-cased, never stripped of their
numbering, never tidied.

Documents do not agree on how to mark a heading, and the corpus already holds
two conventions: the annual report rules its headings with rows of `=`, while
the RAG primer uses markdown `###`.  Rather than branch per file, each
convention is detected across the whole document and the one that actually
occurs wins.  A document using none of them is not an error — it comes back as
a single section, which is the honest description of a document with no
outline.

Where an author marked the structure, that structure wins: it is sized by the
argument being made, which is what a question gets written against.  Where they
marked none there is nothing to detect, and the pipeline's own chunker does the
cutting — token-measured and boundary-preferring, and already relied on
elsewhere.  Writing a second splitter for that case would be the same job done
twice, and the two could disagree about the same document.
"""

import re
from typing import Optional

from app.schemas.golden import DocumentSection, SectionLevel
from app.services.chunker import split_text

# A horizontal rule under or over a heading, as the annual report writes them.
# Eight is short enough to catch a modest underline, long enough that a line of
# dashes inside a table is not mistaken for one.
MIN_RULE_LENGTH = 8

# Longest a line may be and still be read as a heading.  Prose runs longer than
# this; headings almost never do.
MAX_HEADING_LENGTH = 90

# A heading must be followed by at least this much text to stand on its own.
# Below it the "section" is a stray capitalised line, and its text is left with
# the section above rather than split off into a fragment nothing can answer.
MIN_SECTION_CHARS = 40

# Characters of the opening line used to title a block with no heading.
FALLBACK_TITLE_CHARS = 80

# Words that begin a heading in documents that mark them by name rather than by
# punctuation — contracts, statutes, policies. Matched case-insensitively.
HEADING_KEYWORDS = (
    "article",
    "section",
    "chapter",
    "part",
    "appendix",
    "exhibit",
    "schedule",
    "annex",
    "clause",
)

# Appended to a repeated title to keep section titles unique. A golden row
# cites a section by its title and the fact index keys bodies by it, so two
# sections sharing a name would silently check a claim against the wrong text.
DUPLICATE_TITLE_FORMAT = "{title} ({ordinal})"

# Tokens per slice when a document has no headings at all. Sized to a long
# section rather than to an embedding chunk: this is the unit a question gets
# written about, not the unit that gets retrieved.
FALLBACK_SLICE_TOKENS = 400

# Counted to turn a character offset back into a line number.
NEWLINE = "\n"

_RULE = re.compile(rf"^\s*([=\-_*~])\1{{{MIN_RULE_LENGTH - 1},}}\s*$")

# Markdown ATX heading: one to six hashes, then the text.
_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")

# A numbered heading — "3. Findings", "3.1 Consolidated Results". The trailing
# text must not end in a full stop, which is what keeps an ordinary numbered
# sentence out.
_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(\S.*[^.\s])\s*$")


def split_sections(text: str) -> list[DocumentSection]:
    """Split a document into its titled sections.

    Args:
        text: The document's full plain text.

    Returns:
        Every section in document order, each with its heading verbatim, the
        text beneath it, and the titles of any headings nested inside it.  A
        document with no recognisable headings yields exactly one section.
    """
    lines = text.splitlines()
    if not lines:
        return []

    headings = (
        _ruled_headings(lines) or _atx_headings(lines) or _plain_headings(lines)
    )
    if not headings:
        return _slices(text)

    sections = _carve(lines, headings)
    sections = _nest(sections)

    # A heading with nothing under it is a title, not a section — a document's
    # name above its first real heading, most often. It would earn a question
    # quota with no text to draw one from.
    sections = [section for section in sections if section.body.strip()]

    return _unique(sections) if sections else _slices(text)


def titles(sections: list[DocumentSection]) -> list[str]:
    """The titles a golden row is allowed to cite, in document order.

    Args:
        sections: Sections from `split_sections`.

    Returns:
        Every top-level and preamble title. Subsection titles are excluded —
        they are context for drafting, not citable locations.
    """
    return [s.title for s in sections if s.level != SectionLevel.SUB]


def _ruled_headings(lines: list[str]) -> list[tuple[int, int, str, SectionLevel]]:
    """Find headings marked by a rule above, below, or both.

    Args:
        lines: The document's lines.

    Returns:
        Each heading as (heading line, first body line, title, level).
    """
    found: list[tuple[int, int, str, SectionLevel]] = []
    for index, line in enumerate(lines):
        title = line.strip()
        if not title or not _plausible_heading(title):
            continue

        above = index > 0 and _RULE.match(lines[index - 1])
        below = index + 1 < len(lines) and _RULE.match(lines[index + 1])
        if not (above or below):
            continue

        start = index - 1 if above else index
        body_start = index + 2 if below else index + 1
        found.append((start, body_start, title, SectionLevel.TOP))
    return found


def _atx_headings(lines: list[str]) -> list[tuple[int, int, str, SectionLevel]]:
    """Find markdown headings, and work out which depth marks a section.

    Not the shallowest depth: almost every markdown document opens with a single
    `#` title and then uses `##` for its actual sections, and treating the title
    as the only top-level heading folds the whole document into one section.

    So the depth carrying the *most* headings wins. A lone `#` title above five
    `##` sections leaves the `##` level as the sections, and a document whose
    only headings are `###` still works — the author simply started deeper.

    A heading shallower than the chosen depth stays top-level rather than being
    folded away, because the text directly under it would otherwise be lost.

    Args:
        lines: The document's lines.

    Returns:
        Each heading as (heading line, first body line, title, level).
    """
    matches = [(i, _ATX.match(line)) for i, line in enumerate(lines)]
    matches = [(i, m) for i, m in matches if m]
    if not matches:
        return []

    counts: dict[int, int] = {}
    for _, match in matches:
        depth = len(match.group(1))
        counts[depth] = counts.get(depth, 0) + 1

    # Most headings wins; a tie goes to the shallower depth.
    section_depth = min(counts, key=lambda depth: (-counts[depth], depth))

    return [
        (
            i,
            i + 1,
            m.group(2).strip(),
            SectionLevel.SUB
            if len(m.group(1)) > section_depth
            else SectionLevel.TOP,
        )
        for i, m in matches
    ]


def _plain_headings(lines: list[str]) -> list[tuple[int, int, str, SectionLevel]]:
    """Find headings marked by naming or capitalisation rather than punctuation.

    Covers the documents that carry no rules and no markdown but are plainly
    sectioned anyway: "ARTICLE 2 - FEES" in a contract, "DEFINITIONS" as a bare
    capitalised line, "3.1 Eligibility" in a policy.

    A heading has to earn it. The line must be short, must not read as prose,
    and must be followed by enough text to be worth splitting off — otherwise a
    stray capitalised line becomes a section nothing can be asked about.

    Args:
        lines: The document's lines.

    Returns:
        Each heading as (heading line, first body line, title, level).
    """
    found: list[tuple[int, int, str, SectionLevel]] = []

    for index, line in enumerate(lines):
        title = line.strip()
        if not _plausible_heading(title):
            continue

        level = _plain_level(title)
        if level is None:
            continue
        if not _has_body(lines, index):
            continue

        found.append((index, index + 1, title, level))

    return found


def _plain_level(title: str) -> Optional[SectionLevel]:
    """Classify a bare line as a heading, and how deep, or reject it.

    Args:
        title: The stripped line.

    Returns:
        Its level, or None when the line is not a heading.
    """
    numbered = _NUMBERED.match(title)
    if numbered:
        depth = numbered.group(1).count(".")
        return SectionLevel.TOP if depth == 0 else SectionLevel.SUB

    first = title.split(" ", 1)[0].lower().rstrip(".:")
    if first in HEADING_KEYWORDS:
        return SectionLevel.TOP

    # A bare capitalised line, as legal and policy documents write them. Needs a
    # letter in it, or a row of digits from a table would qualify.
    letters = [character for character in title if character.isalpha()]
    if letters and all(character.isupper() for character in letters):
        return SectionLevel.TOP

    return None


def _has_body(lines: list[str], index: int) -> bool:
    """Whether enough text follows a candidate heading to make it one."""
    body = "\n".join(lines[index + 1 :])
    return len(body.strip()) >= MIN_SECTION_CHARS


def _plausible_heading(title: str) -> bool:
    """Whether a line is short enough and shaped like a heading rather than prose."""
    return 0 < len(title) <= MAX_HEADING_LENGTH and not title.endswith((",", ";", ":"))


def _carve(
    lines: list[str], headings: list[tuple[int, int, str, SectionLevel]]
) -> list[DocumentSection]:
    """Cut the document at each heading, keeping any text that precedes the first.

    Args:
        lines: The document's lines.
        headings: Headings from one of the detectors, in document order.

    Returns:
        A section per heading, preceded by a preamble section when the document
        opens with text before its first heading.
    """
    sections: list[DocumentSection] = []

    first = headings[0][0]
    preamble = "\n".join(lines[:first]).strip()
    if len(preamble) >= MIN_SECTION_CHARS:
        sections.append(
            DocumentSection(
                title=_opening_title(lines[:first]),
                level=SectionLevel.PREAMBLE,
                body=preamble,
                start_line=0,
                end_line=first,
            )
        )

    for position, (start, body_start, title, level) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        sections.append(
            DocumentSection(
                title=title,
                level=level,
                body="\n".join(lines[body_start:end]).strip(),
                start_line=start,
                end_line=end,
            )
        )
    return sections


def _nest(sections: list[DocumentSection]) -> list[DocumentSection]:
    """Fold each subsection's title and text up into the section that contains it.

    Subsections are recorded as an outline on their parent rather than returned
    alongside it, so a golden row can only ever cite a location the retriever
    also reports at that granularity.

    Args:
        sections: Sections in document order, subsections included.

    Returns:
        Only top-level and preamble sections, each carrying its own subsections.
    """
    folded: list[DocumentSection] = []
    for section in sections:
        if section.level == SectionLevel.SUB and folded:
            parent = folded[-1]
            parent.subsections.append(section.title)
            parent.body = f"{parent.body}\n\n{section.title}\n{section.body}".strip()
            parent.end_line = section.end_line
            continue
        folded.append(section)
    return folded


def _slices(text: str) -> list[DocumentSection]:
    """Cut a document with no headings using the pipeline's own chunker.

    Delegated rather than hand-rolled. The chunker already measures in tokens —
    which is what actually bounds a prompt, where a character count only
    approximates it — and already prefers a paragraph break, then a sentence
    break, over a hard cut. A second splitter here would be the same job done
    twice and worse, and the two could disagree about the same document.

    Overlap is zero, which is the one place a drafting unit and a retrieval
    chunk want opposite things. Retrieval repeats a few tokens across a
    boundary so a sentence spanning it survives whole in one chunk; drafting
    would read that repetition as more document and ask the same question
    twice.

    Args:
        text: The document's full text.

    Returns:
        One section per slice, titled by its opening line.
    """
    sections: list[DocumentSection] = []

    for content, start_offset, end_offset in split_text(
        text, chunk_size=FALLBACK_SLICE_TOKENS, chunk_overlap=0
    ):
        body = content.strip()
        if not body:
            continue
        sections.append(
            DocumentSection(
                title=_opening_title(body.splitlines()),
                level=SectionLevel.PREAMBLE,
                body=body,
                start_line=text.count(NEWLINE, 0, start_offset),
                end_line=text.count(NEWLINE, 0, end_offset) + 1,
            )
        )

    return _unique(sections)


def _unique(sections: list[DocumentSection]) -> list[DocumentSection]:
    """Make every title distinct, in place, keeping the first occurrence as-is.

    Repeats are ordinary: a report with two "Notes" headings, or blocks cut from
    a document whose paragraphs open alike. Left alone they would break the two
    things a title is used for — citing a section from a golden row, and looking
    that section's text up to check the row against it.

    Args:
        sections: Sections in document order.

    Returns:
        The same sections, with repeated titles numbered.
    """
    seen: dict[str, int] = {}

    for section in sections:
        count = seen.get(section.title, 0) + 1
        seen[section.title] = count
        if count > 1:
            section.title = DUPLICATE_TITLE_FORMAT.format(
                title=section.title, ordinal=count
            )

    return sections


def _opening_title(lines: list[str]) -> str:
    """Title a section that has no heading, using its first line of text."""
    for line in lines:
        stripped = line.strip()
        if stripped and not _RULE.match(line):
            return stripped[:FALLBACK_TITLE_CHARS]
    return "Untitled"
