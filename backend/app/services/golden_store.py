"""What a golden set holds, and how it changes.

Sits on `golden_db` and answers the questions everything else has: give me the
sets, give me this set's rows, record what the generator drafted, record what a
person decided about a row.

Two rules live here rather than anywhere else.

**Q-numbers are assigned, never accepted.**  A model asked to number its own
questions produces collisions and gaps, and the harness keys everything by id.
So ids are stamped on in position order every time the set changes shape, and
the model's opinion about them is discarded.

**A row's own numbering is not its identity.**  `row_id` is what an edit
addresses; `question_id` is what the exported file says.  Dropping row seven
renumbers everything after it, and an API that addressed rows by their
Q-number would silently retarget every pending edit.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.schemas.golden import (
    GoldenDerivation,
    GoldenIssue,
    GoldenReview,
    GoldenRow,
    GoldenRowStatus,
    GoldenRowUpdate,
    GoldenSet,
    GoldenSetDetail,
    GoldenSetState,
)
from app.services import golden_db

logger = logging.getLogger(__name__)

# Sets returned by one listing call. Generous — a set per source file means a
# corpus of this size never comes close.
MAX_PAGE_SIZE = 200

# Format of an exported question id. Zero-padded so the file sorts correctly.
QUESTION_ID_FORMAT = "Q{:03d}"


class UnknownGoldenSet(LookupError):
    """Raised when a set id matches nothing."""


class UnknownGoldenRow(LookupError):
    """Raised when a row id matches nothing in the set it was addressed under."""


async def create(
    source_key: str, slug: str, provider: str, model: str, sections: list[str]
) -> GoldenSet:
    """Open an empty set for a run to fill.

    Args:
        source_key: Object key the set is drafted from.
        slug: Filename stem the set exports under.
        provider: LLM provider doing the drafting.
        model: Model doing the drafting.
        sections: Titles a row in this set may cite.

    Returns:
        The new set, in the drafting state.
    """
    set_id = str(uuid.uuid4())
    now = time.time()

    await asyncio.to_thread(
        golden_db.write,
        """
        INSERT INTO golden_sets
            (set_id, source_key, slug, state, provider, model,
             created_at, updated_at, sections)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            set_id,
            source_key,
            slug,
            GoldenSetState.DRAFTING.value,
            provider,
            model,
            now,
            now,
            json.dumps(sections),
        ),
    )

    return GoldenSet(
        set_id=set_id,
        source_key=source_key,
        slug=slug,
        state=GoldenSetState.DRAFTING,
        provider=provider,
        model=model,
        created_at=_moment(now),
        updated_at=_moment(now),
        sections=sections,
    )


async def replace_rows(set_id: str, rows: list[GoldenRow]) -> list[GoldenRow]:
    """Store the rows a run drafted, replacing anything already there.

    A run fills a set once. Re-running against a source opens a new set rather
    than overwriting the one someone may already have reviewed.

    Args:
        set_id: Set to fill.
        rows: The validated rows, in the order they should be numbered.

    Returns:
        The rows as stored, each with its assigned question id.
    """
    numbered = _renumber(rows)

    await asyncio.to_thread(
        golden_db.write, "DELETE FROM golden_rows WHERE set_id = ?", (set_id,)
    )
    await asyncio.to_thread(
        golden_db.write_many,
        """
        INSERT INTO golden_rows
            (row_id, set_id, position, question_id, type, difficulty, question, answer,
             numeric_answer, numeric_tolerance, answer_keys, forbidden_keys, must_refuse,
             gold_sections, note, derivation, status, issues, review, edited)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [_to_params(set_id, position, row) for position, row in enumerate(numbered)],
    )
    await _touch(set_id)
    return numbered


async def finish(set_id: str, state: GoldenSetState, error: str = "") -> None:
    """Mark a set finished, or failed with a reason.

    Args:
        set_id: Set to close.
        state: Where it ended up.
        error: Why it failed, when it did.
    """
    await asyncio.to_thread(
        golden_db.write,
        "UPDATE golden_sets SET state = ?, error = ?, updated_at = ? WHERE set_id = ?",
        (state.value, error, time.time(), set_id),
    )


async def list_sets(include_deleted: bool = False) -> list[GoldenSet]:
    """Every set, newest first.

    Args:
        include_deleted: Whether withdrawn sets are included.

    Returns:
        The sets, each with its row counts.
    """
    clause = "" if include_deleted else "WHERE s.deleted = 0"
    rows = await asyncio.to_thread(
        golden_db.read,
        f"""
        SELECT s.*,
               (SELECT COUNT(*) FROM golden_rows r
                 WHERE r.set_id = s.set_id) AS row_count,
               (SELECT COUNT(*) FROM golden_rows r
                 WHERE r.set_id = s.set_id AND r.status = 'valid') AS valid_count,
               (SELECT COUNT(*) FROM golden_rows r
                 WHERE r.set_id = s.set_id AND r.review = 'accepted') AS accepted_count
          FROM golden_sets s
          {clause}
         ORDER BY s.created_at DESC
         LIMIT ?
        """,
        (MAX_PAGE_SIZE,),
    )
    return [_to_set(row) for row in rows]


async def get(set_id: str) -> GoldenSetDetail:
    """One set with all of its rows.

    Args:
        set_id: Set to read.

    Returns:
        The set and its rows, in position order.

    Raises:
        UnknownGoldenSet: When no set has that id.
    """
    sets = await asyncio.to_thread(
        golden_db.read,
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM golden_rows r
                 WHERE r.set_id = s.set_id) AS row_count,
               (SELECT COUNT(*) FROM golden_rows r
                 WHERE r.set_id = s.set_id AND r.status = 'valid') AS valid_count,
               (SELECT COUNT(*) FROM golden_rows r
                 WHERE r.set_id = s.set_id AND r.review = 'accepted') AS accepted_count
          FROM golden_sets s WHERE s.set_id = ?
        """,
        (set_id,),
    )
    if not sets:
        raise UnknownGoldenSet(f"No golden set with id {set_id!r}.")

    rows = await rows_for(set_id)
    return GoldenSetDetail(**_to_set(sets[0]).model_dump(), rows=rows)


async def rows_for(set_id: str) -> list[GoldenRow]:
    """Every row in a set, in position order.

    Args:
        set_id: Set to read.

    Returns:
        The rows.
    """
    rows = await asyncio.to_thread(
        golden_db.read,
        "SELECT * FROM golden_rows WHERE set_id = ? ORDER BY position ASC", (set_id,)
    )
    return [_to_row(row) for row in rows]


async def update_row(set_id: str, row_id: str, update: GoldenRowUpdate) -> GoldenRow:
    """Apply an edit or a review decision to one row.

    Only the fields present on the update are touched, so a review decision and
    a text edit can arrive through the same endpoint without either erasing the
    other.

    Args:
        set_id: Set the row belongs to.
        row_id: Row to change.
        update: The fields to change.

    Returns:
        The row as it now stands.

    Raises:
        UnknownGoldenRow: When the row is not in that set.
    """
    existing = await asyncio.to_thread(
        golden_db.read,
        "SELECT * FROM golden_rows WHERE set_id = ? AND row_id = ?", (set_id, row_id)
    )
    if not existing:
        raise UnknownGoldenRow(f"No row {row_id!r} in golden set {set_id!r}.")

    row = _to_row(existing[0])
    changes = update.model_dump(exclude_unset=True, exclude_none=True)

    # A review decision is not an edit to the question. Tracking them apart is
    # what lets the UI show which rows a person actually rewrote.
    content_changed = any(field != "review" for field in changes)
    for field, value in changes.items():
        setattr(row, field, value)
    if content_changed:
        row.edited = True

    await asyncio.to_thread(
        golden_db.write,
        """
        UPDATE golden_rows
           SET type = ?, difficulty = ?, question = ?, answer = ?,
               numeric_answer = ?, numeric_tolerance = ?, answer_keys = ?,
               forbidden_keys = ?, must_refuse = ?, gold_sections = ?, note = ?,
               status = ?, issues = ?, review = ?, edited = ?
         WHERE set_id = ? AND row_id = ?
        """,
        (
            row.type.value,
            row.difficulty.value,
            row.question,
            row.answer,
            row.numeric_answer,
            row.numeric_tolerance,
            json.dumps(row.answer_keys),
            json.dumps(row.forbidden_keys),
            int(row.must_refuse),
            json.dumps(row.gold_sections),
            row.note,
            row.status.value,
            json.dumps([issue.model_dump() for issue in row.issues]),
            row.review.value,
            int(row.edited),
            set_id,
            row_id,
        ),
    )
    await _touch(set_id)
    return row


async def record_check(
    set_id: str, row_id: str, status: GoldenRowStatus, issues: list[GoldenIssue]
) -> None:
    """Store the validator's verdict on one row.

    Kept off `GoldenRowUpdate` on purpose: a client may edit a row's question
    but never declare it valid.  Whether a row holds up is decided by checking
    it against the document, and this is where that answer is written down.

    Args:
        set_id: Set the row belongs to.
        row_id: Row that was checked.
        status: Whether every check passed.
        issues: The checks it did not pass.
    """
    await asyncio.to_thread(
        golden_db.write,
        "UPDATE golden_rows SET status = ?, issues = ? WHERE set_id = ? AND row_id = ?",
        (
            status.value,
            json.dumps([issue.model_dump() for issue in issues]),
            set_id,
            row_id,
        ),
    )


async def set_slug(set_id: str, slug: str) -> None:
    """Rename the file a set exports as."""
    await asyncio.to_thread(
        golden_db.write,
        "UPDATE golden_sets SET slug = ?, updated_at = ? WHERE set_id = ?",
        (slug, time.time(), set_id),
    )


async def withdraw(set_id: str) -> None:
    """Soft-delete a set, keeping it readable.

    Args:
        set_id: Set to withdraw.

    Raises:
        UnknownGoldenSet: When no set has that id.
    """
    changed = await asyncio.to_thread(
        golden_db.write,
        "UPDATE golden_sets SET deleted = 1, deleted_at = ?, updated_at = ? WHERE set_id = ?",
        (time.time(), time.time(), set_id),
    )
    if not changed:
        raise UnknownGoldenSet(f"No golden set with id {set_id!r}.")


async def restore(set_id: str) -> None:
    """Undo a withdrawal."""
    await asyncio.to_thread(
        golden_db.write,
        "UPDATE golden_sets SET deleted = 0, deleted_at = NULL, updated_at = ? WHERE set_id = ?",
        (time.time(), set_id),
    )


async def renumber(set_id: str) -> list[GoldenRow]:
    """Reassign question ids after rows were dropped, so the export has no gaps.

    Args:
        set_id: Set to renumber.

    Returns:
        The rows, in order, with their new ids.
    """
    rows = await rows_for(set_id)
    numbered = _renumber(rows)
    await asyncio.to_thread(
        golden_db.write_many,
        "UPDATE golden_rows SET question_id = ? WHERE row_id = ?",
        [(row.question_id, row.row_id) for row in numbered],
    )
    return numbered


def _renumber(rows: list[GoldenRow]) -> list[GoldenRow]:
    """Stamp Q-numbers on in order, skipping rows a person dropped."""
    counter = 0
    for row in rows:
        if row.review == GoldenReview.DROPPED:
            row.question_id = ""
            continue
        counter += 1
        row.question_id = QUESTION_ID_FORMAT.format(counter)
    return rows


async def _touch(set_id: str) -> None:
    """Record that a set changed."""
    await asyncio.to_thread(
        golden_db.write,
        "UPDATE golden_sets SET updated_at = ? WHERE set_id = ?", (time.time(), set_id)
    )


def _to_params(set_id: str, position: int, row: GoldenRow) -> tuple:
    """Flatten a row into the column order the insert expects."""
    return (
        row.row_id,
        set_id,
        position,
        row.question_id,
        row.type.value,
        row.difficulty.value,
        row.question,
        row.answer,
        row.numeric_answer,
        row.numeric_tolerance,
        json.dumps(row.answer_keys),
        json.dumps(row.forbidden_keys),
        int(row.must_refuse),
        json.dumps(row.gold_sections),
        row.note,
        row.derivation.model_dump_json() if row.derivation else "",
        row.status.value,
        json.dumps([issue.model_dump() for issue in row.issues]),
        row.review.value,
        int(row.edited),
    )


def _to_row(record: dict[str, Any]) -> GoldenRow:
    """Rebuild a row from its stored columns."""
    return GoldenRow(
        row_id=record["row_id"],
        question_id=record["question_id"],
        type=record["type"],
        difficulty=record["difficulty"],
        question=record["question"],
        answer=record["answer"],
        numeric_answer=record["numeric_answer"],
        numeric_tolerance=record["numeric_tolerance"],
        answer_keys=json.loads(record["answer_keys"]),
        forbidden_keys=json.loads(record["forbidden_keys"]),
        must_refuse=bool(record["must_refuse"]),
        gold_sections=json.loads(record["gold_sections"]),
        note=record["note"],
        derivation=(
            GoldenDerivation.model_validate_json(record["derivation"])
            if record["derivation"]
            else None
        ),
        status=GoldenRowStatus(record["status"]),
        issues=[GoldenIssue(**issue) for issue in json.loads(record["issues"])],
        review=GoldenReview(record["review"]),
        edited=bool(record["edited"]),
    )


def _to_set(record: dict[str, Any]) -> GoldenSet:
    """Rebuild a set from its stored columns."""
    return GoldenSet(
        set_id=record["set_id"],
        source_key=record["source_key"],
        slug=record["slug"],
        state=GoldenSetState(record["state"]),
        provider=record["provider"],
        model=record["model"],
        created_at=_moment(record["created_at"]),
        updated_at=_moment(record["updated_at"]),
        row_count=record.get("row_count", 0),
        valid_count=record.get("valid_count", 0),
        accepted_count=record.get("accepted_count", 0),
        sections=json.loads(record["sections"]),
        error=record["error"],
        deleted=bool(record["deleted"]),
    )


def _moment(value: Optional[float]) -> Optional[datetime]:
    """Turn a stored epoch into an aware datetime."""
    return datetime.fromtimestamp(value, tz=timezone.utc) if value else None
