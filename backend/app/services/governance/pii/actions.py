"""What enforce does to a finding: mask, pseudonymize, tag, or reject.

Two properties are load-bearing:

  * Replacement is surgical — spans are swapped right-to-left so earlier
    offsets stay valid, and every byte outside a span survives untouched.
  * Pseudonyms are stable — the same value lands on the same placeholder
    across chunks and documents, or a query about one person retrieves only
    half their files.
"""

import hashlib
import hmac

from app.schemas.governance import (
    Finding,
    GovernanceAction,
    GovernancePolicy,
    SpanEdit,
)

# Keys the pseudonym HMAC. Deliberately a constant for now: stability across
# runs is the requirement. Moving it to config makes pseudonyms
# untraceable without the deployment's secret — do that before real data.
_PSEUDONYM_KEY = b"governance-pseudonym-v1"
_PSEUDONYM_LENGTH = 12


def mask(text: str, findings: list[Finding]) -> str:
    """Replace every finding's span with its type token, e.g. `[EMAIL]`."""
    out = text
    for finding in sorted(findings, key=lambda f: f.start, reverse=True):
        out = out[:finding.start] + f"[{finding.entity_type.name}]" + out[finding.end:]
    return out


def pseudonymize(value: str) -> str:
    """A stable placeholder: same value in, same placeholder out — always."""
    digest = hmac.new(_PSEUDONYM_KEY, value.encode(), hashlib.sha256).hexdigest()
    return f"pii-{digest[:_PSEUDONYM_LENGTH]}"


def apply(
    text: str, findings: list[Finding], policy: GovernancePolicy
) -> tuple[str, str, list[SpanEdit]]:
    """Apply the policy's action per finding; returns (text, verdict, edits).

    A single REJECT finding blocks the whole content — nothing downstream
    should see any of it, so no partial redaction is attempted. The edits
    say which spans were replaced and by how much, so a caller holding
    offsets into the original text can shift them.
    """
    def action_for(finding: Finding) -> GovernanceAction:
        return policy.actions.get(finding.classification, GovernanceAction.TAG)

    if any(action_for(f) is GovernanceAction.REJECT for f in findings):
        return "", "blocked", []

    out = text
    edits: list[SpanEdit] = []
    for finding in sorted(findings, key=lambda f: f.start, reverse=True):
        action = action_for(finding)
        if action is GovernanceAction.MASK:
            replacement = f"[{finding.entity_type.name}]"
        elif action is GovernanceAction.PSEUDONYMIZE:
            replacement = pseudonymize(finding.text)
        else:  # TAG keeps the text; the finding itself is the tag.
            continue
        out = out[:finding.start] + replacement + out[finding.end:]
        edits.append(
            SpanEdit(start=finding.start, end=finding.end, new_length=len(replacement))
        )
    edits.reverse()  # built right-to-left; callers want offset order
    return out, "allowed", edits


def offset_after_edits(edits: list[SpanEdit], position: int) -> int:
    """Where an offset into the original text now falls in the edited text.

    A position inside a replaced span maps to the end of its replacement —
    the nearest place that still exists.
    """
    shifted = position
    for edit in edits:
        if edit.end <= position:
            shifted += edit.new_length - (edit.end - edit.start)
        elif edit.start < position:
            # Inside the replaced span: clamp to the replacement's end.
            shifted += edit.new_length - (position - edit.start)
    return max(0, shifted)
