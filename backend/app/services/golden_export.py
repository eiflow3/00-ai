"""Turns a stored row into the JSONL line the harness reads.

This module owns the wire shape of a golden set, and it is the only place that
knows it.  `evals/run_eval.py` reads these files, and the hand-written
`evals/golden/meridian-fy2025.jsonl` is the reference: a generated set and a
hand-written one must be interchangeable, or the harness has two dialects to
support and no reason to prefer either.

Two rules follow from that.

Absent rather than null.  A row with no numeric answer omits `numeric_answer`
entirely, the way the hand-written rows do.  `null` would score the same but
would make every generated file visibly a different kind of thing.

Nothing internal escapes.  The derivation, the validator's findings and the
review decision are how a row was arrived at, and the harness has no use for
them.  They stop here.
"""

import json
from typing import Any, AsyncIterator, Iterable

from app.schemas.golden import GoldenReview, GoldenRow

# Field order, matching the hand-written set so a diff between a generated file
# and that one shows differences in content rather than in key order.
FIELD_ORDER = (
    "id",
    "type",
    "difficulty",
    "question",
    "answer",
    "numeric_answer",
    "numeric_tolerance",
    "answer_keys",
    "forbidden_keys",
    "must_refuse",
    "gold_sections",
    "note",
)


def build_line(row: GoldenRow) -> dict[str, Any]:
    """Render one row as the harness will read it.

    Args:
        row: The stored row, with all its internal fields.

    Returns:
        Only the fields the harness scores, in the canonical order, with
        optional fields omitted rather than nulled.
    """
    line: dict[str, Any] = {
        "id": row.question_id,
        "type": row.type.value,
        "difficulty": row.difficulty.value,
        "question": row.question,
        "answer": row.answer,
    }

    # Paired: a tolerance without a target scores nothing, so neither appears
    # unless the row actually has a numeric answer.
    if row.numeric_answer is not None:
        line["numeric_answer"] = row.numeric_answer
        line["numeric_tolerance"] = (
            row.numeric_tolerance if row.numeric_tolerance is not None else 0.05
        )

    if row.answer_keys:
        line["answer_keys"] = row.answer_keys
    if row.forbidden_keys:
        line["forbidden_keys"] = row.forbidden_keys
    if row.must_refuse:
        line["must_refuse"] = True

    # Always present, even empty: an unanswerable row has no gold section, and
    # saying so explicitly is how the hand-written set writes it.
    line["gold_sections"] = row.gold_sections

    if row.note:
        line["note"] = row.note

    return {key: line[key] for key in FIELD_ORDER if key in line}


def exportable(rows: Iterable[GoldenRow]) -> list[GoldenRow]:
    """The rows that belong in the file.

    A dropped row is one a person looked at and rejected, so it never ships.
    Everything else does, including rows still pending review — the file is a
    draft until someone says otherwise, and refusing to export it would just
    mean nobody could look at it in the harness.

    Args:
        rows: Every row in the set.

    Returns:
        The rows to write, in order.
    """
    return [row for row in rows if row.review != GoldenReview.DROPPED]


async def stream_jsonl(rows: Iterable[GoldenRow]) -> AsyncIterator[str]:
    """Yield the set one JSONL line at a time.

    Args:
        rows: Every row in the set.

    Yields:
        One newline-terminated JSON object per exportable row.
    """
    for row in exportable(rows):
        yield json.dumps(build_line(row), ensure_ascii=False) + "\n"
