"""Shapes findings into audit records — the privacy boundary of the feature.

A `Finding` carries the raw matched text in process; what any persisted
record may hold is decided HERE by the verbatim knob and nowhere else:

  * OFF     — type, class, confidence and offsets only.  The location is the
              tuning tool: re-open the source at the offsets to see the match.
  * PREVIEW — adds a masked excerpt (`m*********************m`), enough to
              judge a false positive without holding the value.
  * FULL    — adds the raw value, and the record is stamped with the mode it
              was written under so a purge can find it later.

Every record carries the verbatim stamp, so "which records hold raw values"
is a query, not an investigation.
"""

from typing import Any

from app.schemas.governance import Finding, VerbatimMode

# Values this short mask to stars entirely — one visible char each side
# would be most of the value.
_MIN_EXCERPT_LENGTH = 3


def shape(finding: Finding, verbatim: VerbatimMode) -> dict[str, Any]:
    """One finding as the record `run_store` persists."""
    record: dict[str, Any] = {
        "entity_type": finding.entity_type.value,
        "classification": (
            finding.classification.value if finding.classification else None
        ),
        "confidence": finding.confidence,
        "start": finding.start,
        "end": finding.end,
        "verbatim": verbatim.value,
    }
    if verbatim is VerbatimMode.PREVIEW:
        record["preview"] = _excerpt(finding.text)
    elif verbatim is VerbatimMode.FULL:
        record["value"] = finding.text
    return record


def _excerpt(value: str) -> str:
    """First and last character survive; everything between is starred."""
    if len(value) < _MIN_EXCERPT_LENGTH:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]
