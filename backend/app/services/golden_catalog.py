"""What a generation run is allowed to produce, and how much of it.

Two jobs, both about keeping the model honest.

The first is the closed vocabulary — question types, difficulties, check names —
served to the client rather than guessed at, the same decision
`evaluation_catalog` makes for its tags.  A row filed under a type the harness
does not bucket is a row whose score nobody reads.

The second is the quota.  Left to itself a model asked for "a good set of
questions" will pad to whatever number it was shown, inventing figures once the
document runs out of real ones — which is precisely the failure a golden set
cannot survive.  So the quota is computed from the document: how much prose
each section actually holds, and how many figures the document actually states.
A short section gets few questions, and a document with no numbers in it gets
no arithmetic questions at all, because there is nothing to compute.
"""

from dataclasses import dataclass, field

from app.schemas.golden import (
    DocumentSection,
    GoldenDifficulty,
    GoldenQuestionType,
    SectionLevel,
)

# Prose a section must hold to earn one more question. Derived from the corpus:
# the Meridian report holds roughly 12,000 characters of body text and its
# hand-written set asks 24 single-section questions.
CHARS_PER_QUESTION = 500

# A section that exists at all is worth one question; beyond five, questions
# start repeating each other rather than reaching further into the text.
MIN_QUESTIONS_PER_SECTION = 1
MAX_QUESTIONS_PER_SECTION = 5

# Cross-section questions scale with the number of places there are to join.
CROSS_SECTION_PER_SECTION = 1.0
MIN_CROSS_SECTION = 2
MAX_CROSS_SECTION = 12

# Unanswerable questions scale the same way but more slowly — every section
# suggests one adjacent fact the document withholds, not several.
UNANSWERABLE_PER_SECTION = 0.45
MIN_UNANSWERABLE = 2
MAX_UNANSWERABLE = 8

# Distinct figures a document must state before arithmetic questions are asked
# of it. Below this there is nothing to compute, and a model told to compute
# anyway will invent the operands.
MIN_FACTS_FOR_ARITHMETIC = 12

# Types the per-section pass may produce: each is answerable from one section.
SECTION_TYPES: tuple[GoldenQuestionType, ...] = (
    GoldenQuestionType.LOOKUP,
    GoldenQuestionType.TEMPORAL,
    GoldenQuestionType.DISTRACTOR,
    GoldenQuestionType.SYNTHESIS,
)

# Types the cross-section pass may produce: each needs two places at once.
CROSS_SECTION_TYPES: tuple[GoldenQuestionType, ...] = (
    GoldenQuestionType.MULTI_HOP,
    GoldenQuestionType.ARITHMETIC,
)

# The one type the unanswerable pass may produce.
UNANSWERABLE_TYPES: tuple[GoldenQuestionType, ...] = (GoldenQuestionType.UNANSWERABLE,)

# Every check `golden_validator` can report, so a client can label an issue
# without hard-coding the names.
CHECKS: tuple[str, ...] = (
    "keys_verbatim",
    "keys_in_section",
    "numeric_grounded",
    "sections_exist",
    "forbidden_grounded",
    "refusal_shape",
    "no_duplicates",
    "self_check",
)


@dataclass
class SectionQuota:
    """How many questions one section is worth."""

    title: str
    count: int


@dataclass
class GenerationQuota:
    """The whole run's shape, decided before a single model call is made."""

    per_section: list[SectionQuota] = field(default_factory=list)
    cross_section: int = 0
    unanswerable: int = 0

    # False when the document states too few figures to compute anything from.
    allow_arithmetic: bool = True

    @property
    def total(self) -> int:
        """Rows this run will ask for, across every pass."""
        return (
            sum(quota.count for quota in self.per_section)
            + self.cross_section
            + self.unanswerable
        )


def types() -> list[str]:
    """Every question type, in the order a report should bucket them."""
    return [t.value for t in GoldenQuestionType]


def difficulties() -> list[str]:
    """Every difficulty, easiest first."""
    return [d.value for d in GoldenDifficulty]


def checks() -> list[str]:
    """Every validator check a row can fail."""
    return list(CHECKS)


def cross_section_types(allow_arithmetic: bool) -> list[str]:
    """Which cross-section types the drafting prompt may use.

    Args:
        allow_arithmetic: False when the document states too few figures.

    Returns:
        The type names the prompt is permitted to produce.
    """
    if allow_arithmetic:
        return [t.value for t in CROSS_SECTION_TYPES]
    return [t.value for t in CROSS_SECTION_TYPES if t != GoldenQuestionType.ARITHMETIC]


def plan(
    sections: list[DocumentSection], fact_count: int, density: float = 1.0
) -> GenerationQuota:
    """Decide how many questions of each kind this document can support.

    Args:
        sections: Sections from `document_sections.split_sections`.
        fact_count: Distinct figures the document states, from the fact digest.
        density: Multiplier a caller may nudge the quota by.

    Returns:
        The quota for every pass. A document with no citable sections yields an
        empty quota rather than an error — there is simply nothing to ask about.
    """
    citable = [s for s in sections if s.level != SectionLevel.SUB]
    if not citable:
        return GenerationQuota()

    per_section = [
        SectionQuota(title=section.title, count=_section_count(section, density))
        for section in citable
    ]

    return GenerationQuota(
        per_section=per_section,
        cross_section=_scaled(
            len(citable), CROSS_SECTION_PER_SECTION, density, MIN_CROSS_SECTION, MAX_CROSS_SECTION
        ),
        unanswerable=_scaled(
            len(citable), UNANSWERABLE_PER_SECTION, density, MIN_UNANSWERABLE, MAX_UNANSWERABLE
        ),
        allow_arithmetic=fact_count >= MIN_FACTS_FOR_ARITHMETIC,
    )


def _section_count(section: DocumentSection, density: float) -> int:
    """How many questions one section's prose can support."""
    raw = round(section.char_count / CHARS_PER_QUESTION * density)
    return max(MIN_QUESTIONS_PER_SECTION, min(MAX_QUESTIONS_PER_SECTION, raw))


def _scaled(sections: int, rate: float, density: float, low: int, high: int) -> int:
    """Scale a whole-document quota by section count, clamped to a sane range."""
    return max(low, min(high, round(sections * rate * density)))
