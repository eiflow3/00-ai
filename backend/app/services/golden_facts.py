"""An index of everything the source document actually states.

Built once per run, before any model call, and used twice.

The drafting prompts get it as a digest — every figure with the line it sits
on — so the cross-section pass can be asked for arithmetic without being handed
a second copy of the prose it would otherwise quote from.

The validator gets it as a lookup, and this is the more important use.  Every
claim in a drafted row is checked back against this index: an answer key that
is not in the document verbatim, a figure the document never states, a
distractor trap that does not actually exist.  A model drafting a golden set
will occasionally paraphrase a figure or round it, and a rounded figure in an
answer key is a question no correct answer can ever pass.

Numbers are found with the scorer's own regex, deliberately.  If the validator
and the harness disagreed about what counts as a number, a row could be
grounded here and unscoreable there.
"""

from dataclasses import dataclass, field

from app.schemas.golden import DocumentSection, SectionLevel
from app.services.golden_scorer import norm, number_tokens

# Figures quoted into a drafting prompt. Enough to compute across the document,
# short enough to leave the model room to think.
MAX_DIGEST_FACTS = 120

# Characters of the line a figure sits on, kept so the model can see what the
# number means without being given the section.
FACT_CONTEXT_CHARS = 110

# How close two figures must be to count as the same stated fact.
FACT_MATCH_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Fact:
    """One figure the document states, and where it says it."""

    # The number itself, for arithmetic checks.
    value: float

    # How the document writes it — "2,833.0", not "2833.0". This is what an
    # answer key must use.
    token: str

    # The line it appeared on, so the digest reads as facts rather than digits.
    context: str

    # Title of the section it came from.
    section: str


@dataclass
class FactDigest:
    """Everything the document states, indexed for checking and for prompting."""

    facts: tuple[Fact, ...] = ()

    # Normalised whole document, for "does this string appear at all".
    document: str = ""

    # Normalised body per section title, for "does it appear *here*".
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def values(self) -> set[float]:
        """Every distinct figure the document states."""
        return {fact.value for fact in self.facts}

    def contains(self, needle: str) -> bool:
        """Whether the document states this string verbatim.

        Args:
            needle: The answer key or trap to look for.

        Returns:
            True when it appears, ignoring only whitespace and case.
        """
        cleaned = norm(needle)
        return bool(cleaned) and cleaned in self.document

    def section_contains(self, title: str, needle: str) -> bool:
        """Whether one section states this string verbatim.

        Args:
            title: Section title, exactly as the splitter produced it.
            needle: The string to look for.

        Returns:
            True when the section exists and holds the string.
        """
        body = self.sections.get(title)
        cleaned = norm(needle)
        return bool(body and cleaned and cleaned in body)

    def states_number(self, value: float, tolerance: float = FACT_MATCH_TOLERANCE) -> bool:
        """Whether the document states this figure outright.

        Args:
            value: The figure to look for.
            tolerance: How close counts as the same figure.

        Returns:
            True when any stated figure is within tolerance.
        """
        return any(abs(known - value) <= tolerance for known in self.values)

    def render(self, section: str = "") -> str:
        """Format the figures as a digest for a drafting prompt.

        Args:
            section: Limit to one section's figures, or all of them when blank.

        Returns:
            One line per figure, naming the section it came from.
        """
        chosen = [f for f in self.facts if not section or f.section == section]
        lines = [
            f"- {fact.token} ({fact.section}): {fact.context}"
            for fact in chosen[:MAX_DIGEST_FACTS]
        ]
        return "\n".join(lines)


def build(sections: list[DocumentSection]) -> FactDigest:
    """Index every figure and every section's text.

    Args:
        sections: Sections from `document_sections.split_sections`.

    Returns:
        The digest. Sections nested inside another are folded into their parent
        by the splitter, so only citable titles appear as keys here.
    """
    facts: list[Fact] = []
    bodies: dict[str, str] = {}
    whole: list[str] = []

    for section in sections:
        if section.level == SectionLevel.SUB:
            continue

        bodies[section.title] = norm(f"{section.title}\n{section.body}")
        whole.append(f"{section.title}\n{section.body}")
        facts.extend(_facts_in(section))

    return FactDigest(facts=tuple(facts), document=norm("\n".join(whole)), sections=bodies)


def _facts_in(section: DocumentSection) -> list[Fact]:
    """Pull every figure out of one section, keeping the line it sat on."""
    found: list[Fact] = []
    seen: set[tuple[str, str]] = set()

    for line in section.body.splitlines():
        context = " ".join(line.split())[:FACT_CONTEXT_CHARS]
        if not context:
            continue

        for token in number_tokens(line):
            # The same figure on the same line twice is one fact, not two.
            if (token, context) in seen:
                continue
            seen.add((token, context))
            try:
                value = float(token.replace(",", ""))
            except ValueError:
                continue
            found.append(
                Fact(value=value, token=token, context=context, section=section.title)
            )
    return found
