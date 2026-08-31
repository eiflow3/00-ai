"""Evaluations — the judgements made on traced chat requests.

Kept as their own records rather than fields on a trace.  One exchange can be
judged twice (retrieval and generation), judged again later when you change your
mind, and judged by a machine alongside a person — none of which fits a column.

Withdrawing a judgement never deletes it.  The evidence it pointed at is still
evidence, and the fact that a verdict was retracted is itself worth reading.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.schemas.evaluation import (
    Evaluation,
    EvaluationAuthor,
    EvaluationPage,
    EvaluationRequest,
    EvaluationTarget,
    Verdict,
)
from app.services import evaluation_catalog, trace_db

# Largest page the listing endpoints will return, however much is asked for.
MAX_PAGE_SIZE = 200


class TraceNotFound(LookupError):
    """Raised when a judgement names a trace that was never recorded.

    Almost always a trace that aged out of retention, or a client holding an id
    from before the database was reset — not a malformed request.
    """


@dataclass
class Rollup:
    """What a trace scored, condensed for a listing row."""

    # Live judgements attached to the trace.
    count: int = 0

    # Latest live verdict per target, e.g. {"retrieval": "good", "generation": "bad"}.
    verdicts: dict[str, str] = field(default_factory=dict)


def _to_evaluation(row: dict[str, Any]) -> Evaluation:
    """Rebuild an Evaluation from one database row."""
    deleted_at = row.get("deleted_at")
    return Evaluation(
        id=row["id"],
        trace_id=row["trace_id"],
        target=EvaluationTarget(row["target"]),
        verdict=Verdict(row["verdict"]),
        tags=json.loads(row["tags"] or "[]"),
        note=row["note"],
        author=EvaluationAuthor(row["author"]),
        created_at=datetime.fromtimestamp(row["created_at"], tz=timezone.utc),
        deleted=bool(row["deleted"]),
        deleted_at=(
            datetime.fromtimestamp(deleted_at, tz=timezone.utc) if deleted_at else None
        ),
        deleted_reason=row["deleted_reason"],
    )


async def trace_exists(trace_id: str) -> bool:
    """Whether a trace with this id has been recorded."""
    found = await asyncio.to_thread(
        trace_db.read_value,
        "SELECT COUNT(*) FROM traces WHERE trace_id = ?",
        (trace_id,),
    )
    return bool(found)


async def create(trace_id: str, request: EvaluationRequest) -> Evaluation:
    """Record one judgement against one trace.

    Args:
        trace_id: The trace being judged.
        request: The verdict, its target, and any reasons given.

    Returns:
        The stored judgement.

    Raises:
        TraceNotFound: If no trace was recorded under that id.
        ValueError: If a tag is unknown or belongs to a different stage.
    """
    if not await trace_exists(trace_id):
        raise TraceNotFound(
            f"No trace recorded under id {trace_id!r}. It may have aged out of "
            f"retention, or the request was never traced."
        )

    # Validate before writing, so a bad tag cannot half-record a judgement.
    tags = evaluation_catalog.validate_tags(request.target, request.tags)

    evaluation = Evaluation(
        id=uuid.uuid4().hex,
        trace_id=trace_id,
        target=request.target,
        verdict=request.verdict,
        tags=tags,
        note=request.note.strip(),
        author=request.author,
        created_at=datetime.now(timezone.utc),
    )

    await asyncio.to_thread(
        trace_db.write,
        "INSERT INTO evaluations "
        "(id, trace_id, target, verdict, tags, note, author, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            evaluation.id,
            evaluation.trace_id,
            evaluation.target.value,
            evaluation.verdict.value,
            json.dumps(evaluation.tags),
            evaluation.note,
            evaluation.author.value,
            evaluation.created_at.timestamp(),
        ),
    )

    return evaluation


async def get(evaluation_id: str) -> Optional[Evaluation]:
    """Return one judgement, or None if there is no such id."""
    rows = await asyncio.to_thread(
        trace_db.read, "SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)
    )
    return _to_evaluation(rows[0]) if rows else None


async def withdraw(evaluation_id: str, reason: str = "") -> Optional[Evaluation]:
    """Mark one judgement withdrawn, keeping the record.

    Args:
        evaluation_id: The judgement to withdraw.
        reason: Why, if it is worth saying.

    Returns:
        The judgement as it now stands, or None if there is no such id.
    """
    # Guarded on `deleted = 0` so withdrawing twice keeps the first reason and
    # the first timestamp, which are the ones that describe what happened.
    await asyncio.to_thread(
        trace_db.write,
        "UPDATE evaluations SET deleted = 1, deleted_at = ?, deleted_reason = ? "
        "WHERE id = ? AND deleted = 0",
        (datetime.now(timezone.utc).timestamp(), reason.strip(), evaluation_id),
    )
    # Read back rather than trusting the rowcount: zero rows changed means
    # either "no such id" or "already withdrawn", and those differ to a caller.
    return await get(evaluation_id)


async def restore(evaluation_id: str) -> Optional[Evaluation]:
    """Reinstate a withdrawn judgement.

    Args:
        evaluation_id: The judgement to reinstate.

    Returns:
        The judgement as it now stands, or None if there is no such id.
    """
    await asyncio.to_thread(
        trace_db.write,
        "UPDATE evaluations SET deleted = 0, deleted_at = NULL, deleted_reason = '' "
        "WHERE id = ?",
        (evaluation_id,),
    )
    return await get(evaluation_id)


async def list_for_trace(
    trace_id: str, include_deleted: bool = False
) -> list[Evaluation]:
    """Return every judgement on one trace, oldest first.

    Args:
        trace_id: The trace to read.
        include_deleted: Whether withdrawn judgements are included.

    Returns:
        The judgements, in the order they were made.
    """
    clause = "" if include_deleted else " AND deleted = 0"
    rows = await asyncio.to_thread(
        trace_db.read,
        f"SELECT * FROM evaluations WHERE trace_id = ?{clause} "
        f"ORDER BY created_at, rowid",
        (trace_id,),
    )
    return [_to_evaluation(row) for row in rows]


async def list_evaluations(
    limit: int = 50,
    offset: int = 0,
    target: Optional[EvaluationTarget] = None,
    verdict: Optional[Verdict] = None,
    author: Optional[EvaluationAuthor] = None,
    tag: Optional[str] = None,
    include_deleted: bool = False,
) -> EvaluationPage:
    """Return one page of judgements across every trace, newest first.

    Args:
        limit: Page size, capped at MAX_PAGE_SIZE.
        offset: Rows to skip before the page.
        target: Only judgements of this stage.
        verdict: Only judgements with this verdict.
        author: Only judgements from a person, or from a machine.
        tag: Only judgements carrying this reason.
        include_deleted: Whether withdrawn judgements are included.

    Returns:
        The page, with the total number of matching rows for pagination.
    """
    size = max(1, min(limit, MAX_PAGE_SIZE))
    clauses: list[str] = []
    values: list[Any] = []

    if not include_deleted:
        clauses.append("deleted = 0")
    if target is not None:
        clauses.append("target = ?")
        values.append(target.value)
    if verdict is not None:
        clauses.append("verdict = ?")
        values.append(verdict.value)
    if author is not None:
        clauses.append("author = ?")
        values.append(author.value)
    if tag:
        # Tags are a JSON array; the quoted form cannot match a partial id.
        clauses.append("tags LIKE ?")
        values.append(f'%"{tag}"%')

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = await asyncio.to_thread(
        trace_db.read_value, f"SELECT COUNT(*) FROM evaluations {where}", tuple(values)
    )
    rows = await asyncio.to_thread(
        trace_db.read,
        f"SELECT * FROM evaluations {where} ORDER BY created_at DESC, rowid DESC "
        f"LIMIT ? OFFSET ?",
        (*values, size, max(0, offset)),
    )

    return EvaluationPage(
        evaluations=[_to_evaluation(row) for row in rows],
        total=int(total),
        limit=size,
        offset=max(0, offset),
    )


async def rollup(trace_ids: list[str]) -> dict[str, Rollup]:
    """Condense each trace's live judgements into one row's worth of verdict.

    Args:
        trace_ids: The traces to summarise.

    Returns:
        A rollup per trace that has at least one live judgement. A trace with
        none is simply absent, rather than carrying an empty rollup.
    """
    if not trace_ids:
        return {}

    placeholders = ", ".join("?" for _ in trace_ids)
    rows = await asyncio.to_thread(
        trace_db.read,
        f"SELECT trace_id, target, verdict FROM evaluations "
        f"WHERE deleted = 0 AND trace_id IN ({placeholders}) "
        f"ORDER BY created_at, rowid",
        tuple(trace_ids),
    )

    summaries: dict[str, Rollup] = {}
    for row in rows:
        summary = summaries.setdefault(row["trace_id"], Rollup())
        summary.count += 1
        # Ordered oldest first, so the last write per target wins — a later
        # judgement of the same stage supersedes an earlier one on the row.
        summary.verdicts[row["target"]] = row["verdict"]

    return summaries
