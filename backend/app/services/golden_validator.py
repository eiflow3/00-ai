"""Grounds every drafted row in the source document, or says why it cannot.

This is the check that makes a generated golden set worth having.  A model
asked for evaluation questions writes plausible ones, and plausible is not the
standard: a golden set is the thing every future score is measured against, so
a single wrong answer key silently marks correct answers wrong from then on,
and nobody finds out.  The model drafts; this decides what is true.

Every check compares a row against the document rather than against itself.  An
answer key must be a string the document actually uses.  A figure must be one
the document actually states, or one that can be recomputed from figures it
does.  A distractor's trap must be a real near-miss and not an invented one.
A cited section must exist.

The last check is the strongest, and the cheapest: each row is scored against
its own reference answer using the harness's own scorer.  A golden row whose
answer does not pass its own test is unusable — that is exactly what
`evals/predictions/example-perfect.jsonl` proves about the hand-written set,
and a generated set has to clear the same bar.  It is why this module imports
`golden_export`: the row that gets scored is the row that will ship, not a
tidier version of it.

Nothing is deleted here.  A row that fails is returned flagged, with the reason
attached, because most failures are a good question with one bad field and a
person can fix that in a moment.  Silently dropping them would hide both the
question and the fact that the model keeps getting that field wrong.
"""

import logging
from typing import Optional

from app.schemas.golden import (
    GoldenDerivation,
    GoldenIssue,
    GoldenQuestionType,
    GoldenRow,
    GoldenRowStatus,
)
from app.services.golden_export import build_line
from app.services.golden_facts import FactDigest
from app.services.golden_scorer import norm, score_row

logger = logging.getLogger(__name__)

# How close a recomputed figure must land to the answer it claims. Generous
# next to a stated figure's exact match, because a derived percentage is
# legitimately rounded in the answer prose.
DERIVATION_TOLERANCE = 0.25

# Two questions this similar are the same question asked twice.
DUPLICATE_SIMILARITY = 0.85

# Shortest question worth comparing for duplication.
MIN_DUPLICATE_LENGTH = 12

# How a derivation's operands combine. Anything else is rejected rather than
# guessed at — a derivation nobody can recompute proves nothing.
OPERATORS = ("sum", "difference", "ratio", "percent_of", "percent_change")


def validate(rows: list[GoldenRow], digest: FactDigest, sections: list[str]) -> list[GoldenRow]:
    """Check every row against the document, flagging what does not hold.

    Args:
        rows: The drafted rows, in document order.
        digest: The document's facts, from `golden_facts.build`.
        sections: Titles a row is allowed to cite.

    Returns:
        The same rows, each with its issues attached and its status set.
    """
    known = set(sections)
    checked: list[GoldenRow] = []

    for row in rows:
        issues: list[GoldenIssue] = []
        issues += _check_sections(row, known)
        issues += _check_keys(row, digest)
        issues += _check_numeric(row, digest)
        issues += _check_forbidden(row, digest)
        issues += _check_refusal(row)
        issues += _check_self_score(row)

        row.issues = issues
        row.status = GoldenRowStatus.FLAGGED if issues else GoldenRowStatus.VALID
        checked.append(row)

    _flag_duplicates(checked)
    return checked


def _check_sections(row: GoldenRow, known: set[str]) -> list[GoldenIssue]:
    """Every cited section must be one the splitter actually produced."""
    return [
        GoldenIssue(
            check="sections_exist",
            detail=f"cites {title!r}, which is not a section of this document",
        )
        for title in row.gold_sections
        if title not in known
    ]


def _check_keys(row: GoldenRow, digest: FactDigest) -> list[GoldenIssue]:
    """Answer keys must be verbatim from the document, and from the right part of it.

    A paraphrased key is the quiet failure this catches: it reads correctly,
    and no answer can ever contain it.
    """
    issues: list[GoldenIssue] = []

    for key in row.answer_keys:
        if not digest.contains(key):
            issues.append(
                GoldenIssue(
                    check="keys_verbatim",
                    detail=f"answer key {key!r} does not appear in the document",
                )
            )
            continue

        # Only meaningful once we know the key exists and the row cites
        # somewhere. An unanswerable row cites nothing, and correctly so.
        if row.gold_sections and not any(
            digest.section_contains(title, key) for title in row.gold_sections
        ):
            issues.append(
                GoldenIssue(
                    check="keys_in_section",
                    detail=f"answer key {key!r} appears in the document but not in {row.gold_sections}",
                )
            )

    return issues


def _check_numeric(row: GoldenRow, digest: FactDigest) -> list[GoldenIssue]:
    """A numeric answer must be stated by the document or derivable from it."""
    if row.numeric_answer is None:
        return []

    if digest.states_number(row.numeric_answer):
        return []

    computed = _recompute(row.derivation)
    if computed is None:
        return [
            GoldenIssue(
                check="numeric_grounded",
                detail=(
                    f"{row.numeric_answer} is not stated in the document and "
                    "carries no derivation to recompute it from"
                ),
            )
        ]

    ungrounded = [
        operand
        for operand in (row.derivation.operands if row.derivation else [])
        if not digest.states_number(operand)
    ]
    if ungrounded:
        return [
            GoldenIssue(
                check="numeric_grounded",
                detail=f"derivation uses figures the document never states: {ungrounded}",
            )
        ]

    if abs(computed - row.numeric_answer) > DERIVATION_TOLERANCE:
        return [
            GoldenIssue(
                check="numeric_grounded",
                detail=f"derivation computes {computed:.4g}, but the answer claims {row.numeric_answer}",
            )
        ]

    return []


def _recompute(derivation: Optional[GoldenDerivation]) -> Optional[float]:
    """Recompute a derived figure from its operands.

    Args:
        derivation: The working the model showed, if it showed any.

    Returns:
        The recomputed figure, or None when there is nothing to recompute or
        the operator is not one we can check.
    """
    if derivation is None or derivation.operator not in OPERATORS:
        return None

    operands = derivation.operands
    if not operands:
        return None

    try:
        if derivation.operator == "sum":
            return sum(operands)
        if derivation.operator == "difference":
            return operands[0] - sum(operands[1:])
        if derivation.operator == "ratio":
            return operands[0] / operands[1]
        if derivation.operator == "percent_of":
            return operands[0] / operands[1] * 100
        if derivation.operator == "percent_change":
            return (operands[0] - operands[1]) / operands[1] * 100
    except (IndexError, ZeroDivisionError):
        return None
    return None


def _check_forbidden(row: GoldenRow, digest: FactDigest) -> list[GoldenIssue]:
    """A trap must be a real near-miss in the document, and must not be sprung."""
    issues: list[GoldenIssue] = []
    low = norm(row.answer)

    for key in row.forbidden_keys:
        if not digest.contains(key):
            issues.append(
                GoldenIssue(
                    check="forbidden_grounded",
                    detail=f"forbidden key {key!r} is not in the document, so it traps nothing",
                )
            )
        if norm(key) and norm(key) in low:
            issues.append(
                GoldenIssue(
                    check="forbidden_grounded",
                    detail=f"the reference answer itself contains forbidden key {key!r}",
                )
            )

    return issues


def _check_refusal(row: GoldenRow) -> list[GoldenIssue]:
    """The unanswerable type and the must_refuse flag have to agree.

    They are the same claim written twice — one for the report's buckets, one
    for the scorer — and a row where they disagree is scored as the opposite of
    what it is filed as.
    """
    unanswerable = row.type == GoldenQuestionType.UNANSWERABLE

    if unanswerable and not row.must_refuse:
        return [
            GoldenIssue(
                check="refusal_shape",
                detail="typed unanswerable but must_refuse is not set, so refusing would score wrong",
            )
        ]
    if row.must_refuse and not unanswerable:
        return [
            GoldenIssue(
                check="refusal_shape",
                detail=f"must_refuse is set on a {row.type.value} row",
            )
        ]
    return []


def _check_self_score(row: GoldenRow) -> list[GoldenIssue]:
    """Score the row against its own reference answer, as the harness would.

    The strongest check available, because it uses the real scorer rather than
    an approximation of it. A row that cannot pass its own test would mark
    every correct answer wrong.
    """
    gold = build_line(row)
    result = score_row(gold, {"answer": row.answer})
    if result["correct"]:
        return []

    return [
        GoldenIssue(
            check="self_check",
            detail=f"the reference answer fails its own row: {'; '.join(result['reasons'])}",
        )
    ]


def _flag_duplicates(rows: list[GoldenRow]) -> None:
    """Flag rows asking the same question twice, in place.

    Compared on word overlap rather than character distance, because two
    questions about the same fact are usually reworded rather than retyped.
    """
    seen: list[tuple[GoldenRow, set[str]]] = []

    for row in rows:
        words = set(norm(row.question).split())
        if len(row.question) < MIN_DUPLICATE_LENGTH or not words:
            continue

        for other, other_words in seen:
            overlap = len(words & other_words) / max(len(words), len(other_words))
            if overlap >= DUPLICATE_SIMILARITY:
                row.issues.append(
                    GoldenIssue(
                        check="no_duplicates",
                        detail=f"asks nearly the same question as {other.question_id or other.row_id}",
                    )
                )
                row.status = GoldenRowStatus.FLAGGED
                break

        seen.append((row, words))
