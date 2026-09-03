"""Pass 2: decide whose data each finding is.

Personal vs business is never a property of the string — the same email
shape is a customer's private address or a company's published contact.  The
call is made from three signals, in order of authority:

  1. the policy's own-domain allowlist (an operator statement beats any
     heuristic),
  2. the shape of the address itself (role local-parts, free-mail domains),
  3. the words around the finding ("my personal mobile" vs "our office line").

A named corporate address stays AMBIGUOUS on purpose: that is the genuinely
undecidable class, and policy — not this module — owns its default.
"""

import re

from app.schemas.governance import Finding, GovernancePolicy, PiiClass

# Local parts that address a function, not a person.
ROLE_LOCALS = frozenset({
    "admin", "billing", "careers", "contact", "hello", "help", "hr", "info",
    "no-reply", "noreply", "office", "press", "sales", "service", "support",
    "team",
})

# Domains where an address is somebody's own, not their employer's.
FREE_MAIL_DOMAINS = frozenset({
    "aol.com", "gmail.com", "gmx.com", "hotmail.com", "icloud.com",
    "live.com", "mail.com", "msn.com", "outlook.com", "proton.me",
    "protonmail.com", "yahoo.com", "yandex.com", "zoho.com",
})

# Words near a finding that tip the personal-vs-business call.
PERSONAL_CUES = frozenset({
    "cell", "her", "his", "home", "lives", "mobile", "my", "personal",
    "private", "residential",
})
BUSINESS_CUES = frozenset({
    "company", "desk", "headquarters", "hotline", "line", "main", "office",
    "our", "published", "service", "support",
})

# How far back the context window reaches, in characters.
CONTEXT_WINDOW = 40

_WORDS = re.compile(r"[a-z]+")


def classify(
    text: str, findings: list[Finding], policy: GovernancePolicy
) -> list[Finding]:
    """Return the findings with their classification decided."""
    return [
        finding.model_copy(update={"classification": _class_for(finding, text, policy)})
        for finding in findings
    ]


def _class_for(finding: Finding, text: str, policy: GovernancePolicy) -> PiiClass:
    kind = finding.entity_type.value
    if kind == "email":
        return _classify_email(finding.text, policy)
    if kind in ("phone", "address"):
        return _classify_by_context(finding, text)
    if kind == "ip_address":
        return PiiClass.INFRA
    # SSN, card, name, date of birth: always somebody's personal data.
    return PiiClass.PERSONAL


def _classify_email(address: str, policy: GovernancePolicy) -> PiiClass:
    local, _, domain = address.rpartition("@")
    domain = domain.lower()
    if domain in {d.lower() for d in policy.own_domains}:
        return PiiClass.BUSINESS
    if local.lower() in ROLE_LOCALS:
        return PiiClass.BUSINESS
    if domain in FREE_MAIL_DOMAINS:
        return PiiClass.PERSONAL
    return PiiClass.AMBIGUOUS


def _classify_by_context(finding: Finding, text: str) -> PiiClass:
    window = text[max(0, finding.start - CONTEXT_WINDOW):finding.start].lower()
    words = set(_WORDS.findall(window))
    personal = len(words & PERSONAL_CUES)
    business = len(words & BUSINESS_CUES)
    if personal > business:
        return PiiClass.PERSONAL
    if business > personal:
        return PiiClass.BUSINESS
    return PiiClass.AMBIGUOUS
