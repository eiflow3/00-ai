"""The local database behind chat traces and their evaluations.

Owns the connection, the schema and the retention rule — nothing about what a
trace or an evaluation *means*.  `trace_store` and `evaluation_store` sit on top
and answer those questions.

Kept apart from `run_store`'s database on purpose.  Run history is a rolling
window that prunes at thirty days; an evaluated trace is a judgement someone
made, and must not evaporate because a month passed.  Two files means the
retention rules can differ without either one having to know about the other.

``sqlite3`` is synchronous, so every call is handed to a worker thread like the
other blocking clients in this package.
"""

import asyncio
import logging
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# How long an *unjudged* trace is kept.  Long enough to evaluate last week's
# answers, short enough that idle chatter does not accumulate forever.  A trace
# carrying any evaluation is exempt: someone looked at it, so it is evidence.
TRACE_RETENTION_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id        TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    question        TEXT NOT NULL DEFAULT '',
    answer          TEXT NOT NULL DEFAULT '',
    provider        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    temperature     REAL NOT NULL DEFAULT 1.0,
    system_prompt   TEXT NOT NULL DEFAULT '',
    use_rag         INTEGER NOT NULL DEFAULT 1,
    top_k           INTEGER NOT NULL DEFAULT 0,
    score_threshold REAL NOT NULL DEFAULT 0,
    embedding_model TEXT NOT NULL DEFAULT '',
    total_searched  INTEGER NOT NULL DEFAULT 0,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    top_score       REAL NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'completed',
    error_stage     TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    retrieval_ms    INTEGER NOT NULL DEFAULT 0,
    generation_ms   INTEGER NOT NULL DEFAULT 0,
    total_ms        INTEGER NOT NULL DEFAULT 0,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    total_cost      REAL NOT NULL DEFAULT 0
);

-- The chunks are stored verbatim rather than by reference. A vector id names a
-- slot, so a re-index at a different chunk size would silently repoint it at
-- other text — and a trace exists precisely to be read after that happens.
CREATE TABLE IF NOT EXISTS trace_chunks (
    trace_id     TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    chunk_id     TEXT NOT NULL DEFAULT '',
    document_id  TEXT NOT NULL DEFAULT '',
    source_key   TEXT NOT NULL DEFAULT '',
    score        REAL NOT NULL DEFAULT 0,
    content      TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    char_count   INTEGER NOT NULL DEFAULT 0,
    dropped      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trace_id, rank)
);

-- Separate from the trace so one exchange can be judged more than once, and so
-- withdrawing a judgement never touches the evidence it was about.
CREATE TABLE IF NOT EXISTS evaluations (
    id             TEXT PRIMARY KEY,
    trace_id       TEXT NOT NULL,
    target         TEXT NOT NULL,
    verdict        TEXT NOT NULL,
    tags           TEXT NOT NULL DEFAULT '[]',
    note           TEXT NOT NULL DEFAULT '',
    author         TEXT NOT NULL DEFAULT 'human',
    created_at     REAL NOT NULL,
    deleted        INTEGER NOT NULL DEFAULT 0,
    deleted_at     REAL,
    deleted_reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS traces_created_at ON traces (created_at DESC);
CREATE INDEX IF NOT EXISTS trace_chunks_source ON trace_chunks (source_key);
CREATE INDEX IF NOT EXISTS evaluations_trace ON evaluations (trace_id);
CREATE INDEX IF NOT EXISTS evaluations_created_at ON evaluations (created_at DESC);
"""

# One connection shared across the thread pool.  SQLite serialises writes
# itself, but the Python object is not thread-safe, so a lock guards every use.
_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """Open the trace database, creating it and its schema if needed."""
    global _connection

    if _connection is not None:
        return _connection

    settings.trace_history_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        settings.trace_history_path,
        # Handed between threads by asyncio.to_thread, guarded by _lock.
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    # WAL lets the traces screen read while a chat request is still writing.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(_SCHEMA)
    connection.commit()

    _connection = connection
    return connection


def write(statement: str, parameters: tuple = ()) -> int:
    """Run one write statement under the connection lock.

    Args:
        statement: The SQL to execute.
        parameters: Values to bind into it.

    Returns:
        How many rows the statement changed.
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


def read_value(statement: str, parameters: tuple = (), default: Any = 0) -> Any:
    """Run one query and return the first column of its first row."""
    rows = read(statement, parameters)
    if not rows:
        return default
    return next(iter(rows[0].values()), default)


def _prune_sync() -> int:
    """Delete unjudged traces past the retention window.

    A trace with any evaluation attached — including a withdrawn one — is kept
    regardless of age.  Withdrawing a verdict is not the same as saying the
    exchange never mattered.
    """
    cutoff = time.time() - TRACE_RETENTION_DAYS * 24 * 60 * 60
    keep = "SELECT DISTINCT trace_id FROM evaluations"

    with _lock:
        connection = _connect()
        connection.execute(
            f"DELETE FROM trace_chunks WHERE trace_id IN "
            f"(SELECT trace_id FROM traces WHERE created_at < ? AND trace_id NOT IN ({keep}))",
            (cutoff,),
        )
        removed = connection.execute(
            f"DELETE FROM traces WHERE created_at < ? AND trace_id NOT IN ({keep})",
            (cutoff,),
        ).rowcount
        connection.commit()

    return removed


async def initialise() -> None:
    """Prepare the trace database. Safe to call once at startup."""
    removed = await asyncio.to_thread(_prune_sync)
    if removed:
        logger.info("Pruned %d unevaluated chat trace(s) past retention.", removed)
    logger.info("Trace history ready at %s", settings.trace_history_path)
