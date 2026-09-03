"""Pass 1: find candidate PII in text.

Every finding's offsets index into the exact text given — a finding that
cannot be located cannot be masked, so offsets are the contract, not a
convenience.  Overlapping candidates are resolved earliest-then-longest,
which is what collapses an email inside a mailto: URL to one finding.
"""

from app.schemas.governance import Finding

from .recognizers import RECOGNIZERS


def detect(text: str) -> list[Finding]:
    """Return every validated candidate, non-overlapping, in text order."""
    candidates: list[Finding] = []
    for recognizer in RECOGNIZERS:
        for match in recognizer.pattern.finditer(text):
            value = match.group()
            if recognizer.validator and not recognizer.validator(value, match.start(), text):
                continue
            candidates.append(
                Finding(
                    entity_type=recognizer.entity_type,
                    text=value,
                    start=match.start(),
                    end=match.end(),
                    confidence=recognizer.confidence,
                )
            )

    # Earliest first; at the same start the longer match wins.
    candidates.sort(key=lambda finding: (finding.start, -(finding.end - finding.start)))

    accepted: list[Finding] = []
    cursor = -1
    for finding in candidates:
        if finding.start >= cursor:
            accepted.append(finding)
            cursor = finding.end
    return accepted
