"""The local database behind generated golden sets.

Owns the connection and the schema — nothing about what a row *means*, which is
`golden_store`'s job, and nothing about whether a row is true, which is
`golden_validator`'s.

Its own file, and no retention rule at all.  The other two databases both
prune: run history at thirty days, and unjudged traces with it.  A golden set
is neither history nor chatter — it is the answer key every future score is
measured against, and a set that quietly expired would take the meaning of
every past run with it.  It is closer to an edited prompt than to a run: it is
configuration, and it goes when someone deletes it.

Deletion is soft, for the same reason it is soft for evaluations.  A set
someone spent an afternoon reviewing should survive a misclick, and a set that
was used to score a run must still be readable when someone asks what that run
was actually measured against.

``sqlite3`` is synchronous, so every call is handed to a worker thread like the
other blocking clients in this package.
"""

import asyncio
import logging
import sqlite3
import threading
from typing import Any, Iterable, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS golden_sets (
    set_id         TEXT PRIMARY KEY,
    source_key     TEXT NOT NULL DEFAULT '',
    slug           TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL DEFAULT 'drafting',
    provider       TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    sections       TEXT NOT NULL DEFAULT '[]',
    error          TEXT NOT NULL DEFAULT '',
    deleted        INTEGER NOT NULL DEFAULT 0,
    deleted_at     REAL
);

-- One row per question. The exported fields and the internal ones sit in the
-- same table because they describe the same object; `golden_export` decides
-- which of them leave the building.
--
-- `position` rather than the Q-number is the ordering key: the Q-number is
-- reassigned whenever a row is dropped, and an ordering that renumbers itself
-- cannot be an ordering.
CREATE TABLE IF NOT EXISTS golden_rows (
    row_id            TEXT PRIMARY KEY,
    set_id            TEXT NOT NULL,
    position          INTEGER NOT NULL DEFAULT 0,
    question_id       TEXT NOT NULL DEFAULT '',
    type              TEXT NOT NULL DEFAULT 'lookup',
    difficulty        TEXT NOT NULL DEFAULT 'medium',
    question          TEXT NOT NULL DEFAULT '',
    answer            TEXT NOT NULL DEFAULT '',
    numeric_answer    REAL,
    numeric_tolerance REAL,
    answer_keys       TEXT NOT NULL DEFAULT '[]',
    forbidden_keys    TEXT NOT NULL DEFAULT '[]',
    must_refuse       INTEGER NOT NULL DEFAULT 0,
    gold_sections     TEXT NOT NULL DEFAULT '[]',
    note              TEXT NOT NULL DEFAULT '',
    derivation        TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'valid',
    issues            TEXT NOT NULL DEFAULT '[]',
    review            TEXT NOT NULL DEFAULT 'pending',
    edited            INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (set_id) REFERENCES golden_sets (set_id)
);

CREATE INDEX IF NOT EXISTS idx_golden_rows_set ON golden_rows (set_id, position);
CREATE INDEX IF NOT EXISTS idx_golden_sets_source ON golden_sets (source_key, created_at DESC);
"""

# One connection shared across the thread pool.  SQLite serialises writes
# itself, but the Python object is not thread-safe, so a lock guards every use.
_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """Open the golden set database, creating it and its schema if needed."""
    global _connection

    if _connection is not None:
        return _connection

    settings.golden_store_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        settings.golden_store_path,
        # Handed between threads by asyncio.to_thread, guarded by _lock.
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_SCHEMA)
    connection.commit()

    _connection = connection
    return connection


def write(statement: str, parameters: tuple = ()) -> int:
    """Run one write statement under the connection lock.

    Args:
        statement: The SQL to run.
        parameters: Values to bind.

    Returns:
        How many rows it changed.
    """
    with _lock:
        connection = _connect()
        cursor = connection.execute(statement, parameters)
        connection.commit()
        return cursor.rowcount


def write_many(statement: str, rows: Iterable[tuple]) -> None:
    """Run one write statement over many parameter tuples, in a transaction."""
    batch = list(rows)
    if not batch:
        return

    with _lock:
        connection = _connect()
        connection.executemany(statement, batch)
        connection.commit()


def read(statement: str, parameters: tuple = ()) -> list[dict[str, Any]]:
    """Run one query under the connection lock, returning plain dicts."""
    with _lock:
        connection = _connect()
        rows = connection.execute(statement, parameters).fetchall()
    return [dict(row) for row in rows]


async def initialise() -> None:
    """Prepare the golden set database. Safe to call once at startup."""
    await asyncio.to_thread(_connect)
    logger.info("Golden sets ready at %s", settings.golden_store_path)
