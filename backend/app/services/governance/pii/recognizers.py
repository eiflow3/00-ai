"""One recognizer per entity type: a pattern, a confidence, and (where shape
alone lies) a validator.

The validators are where the false-positive traps die:

  * a card number must pass Luhn, or every sixteen-digit order id fires;
  * an IP must have sane octets AND not sit after "version"/"build",
    because `10.4.0.1` is a release number in half the documents we index;
  * SSN and card patterns carry lookarounds so a match inside a longer
    token (`ORD-000-12-3456`) never starts.

Phone patterns accept a page marker (`<!-- page N -->`) inside the gap
between digit groups: PDF-derived text breaks entities across pages, and an
entity split by extraction is still one entity.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

from app.schemas.governance import EntityType

# Text between digit groups of one phone number: whitespace, dashes, and the
# page marker that derived markdown inserts between PDF pages.
_PAGE_MARKER = r"<!--\s*page\s+\d+\s*-->"
_GAP = rf"(?:[\s\-]|{_PAGE_MARKER})*"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# International: +CC then 2-4 digit groups. Local US: (NNN) NNN-NNNN. A bare
# NNN-NNNN is deliberately NOT a phone — that shape is every part number.
_PHONE = re.compile(
    rf"(?:\+\d{{1,3}}(?:{_GAP}(?:\(\d{{2,4}}\)|\d{{2,4}})){{2,4}}"
    rf"|\(\d{{3}}\)\s*\d{{3}}[-\s]\d{{4}})(?!\d)"
)

_SSN = re.compile(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])")

_CARD = re.compile(
    r"(?<![\d-])(?:\d{4}(?:[ -]\d{4}){2}[ -]\d{1,4}|\d{13,19})(?![\d-])"
)

_IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")

# Words that, just before an IP-shaped match, mean it is a release number.
_VERSION_CONTEXT = re.compile(r"(?:version|build|release)[\s:`'\"]*$")
_CONTEXT_LOOKBACK = 24


def _luhn(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        digit = int(char)
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _card_is_valid(value: str, start: int, text: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 13 <= len(digits) <= 19 and _luhn(digits)


def _ip_is_valid(value: str, start: int, text: str) -> bool:
    if any(int(octet) > 255 for octet in value.split(".")):
        return False
    window = text[max(0, start - _CONTEXT_LOOKBACK):start].lower()
    return not _VERSION_CONTEXT.search(window)


@dataclass(frozen=True)
class Recognizer:
    """One entity type's pattern, plus the validator that keeps it honest."""

    entity_type: EntityType
    pattern: re.Pattern[str]
    confidence: float
    validator: Optional[Callable[[str, int, str], bool]] = None


RECOGNIZERS: tuple[Recognizer, ...] = (
    Recognizer(EntityType.EMAIL, _EMAIL, confidence=0.95),
    Recognizer(EntityType.PHONE, _PHONE, confidence=0.85),
    Recognizer(EntityType.SSN, _SSN, confidence=0.9),
    Recognizer(EntityType.CREDIT_CARD, _CARD, confidence=0.98, validator=_card_is_valid),
    Recognizer(EntityType.IP_ADDRESS, _IP, confidence=0.9, validator=_ip_is_valid),
)
