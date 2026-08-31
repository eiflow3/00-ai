"""Run history — a durable record of what the indexing pipeline did.

The queue and its worker keep live progress in memory, which is the right place
for it: after a restart there is no run left to report on.  What memory cannot
answer is the question asked afterwards — *which files failed last night, and
why* — so every run is also written here, to a SQLite file on disk.

SQLite rather than a server: there is one process, and the need is to look at
the past rather than share the present.  It ships with Python, so this adds no
dependency and nothing to run.  ``sqlite3`` is synchronous, so every call is
handed to a worker thread like the other blocking clients in this package.

Three tables, answering three different questions:
  * ``runs``       — what happened, at a glance;
  * ``run_files``  — what happened to each file, including how much embedding
                     work was skipped because the index already held it;
  * ``run_events`` — the full event trace, so a finished run stays inspectable
                     long after the process that streamed it is gone.
"""

import asyncio
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# How long a run's record is kept.  Long enough to look back over a month of
# work, short enough that the file does not grow without bound.
RUN_HISTORY_DAYS = 30

# Terminal state given to a run that was still marked running at startup.  The
# process that owned it is gone, so it did not complete and did not fail — it
# was abandoned, and saying so is more honest than leaving it "running".
STATE_ABANDONED = "abandoned"
STATE_RUNNING = "running"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    job_id          TEXT PRIMARY KEY,
    state           TEXT NOT NULL,
    started_at      REAL NOT NULL,
    finished_at     REAL,
    embedding_model TEXT NOT NULL DEFAULT '',
    chunk_size      INTEGER NOT NULL DEFAULT 0,
    chunk_overlap   INTEGER NOT NULL DEFAULT 0,
    queued_keys     TEXT NOT NULL DEFAULT '[]',
    indexed         INTEGER NOT NULL DEFAULT 0,
    skipped         INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    total_chunks    INTEGER NOT NULL DEFAULT 0,
    total_reused    INTEGER NOT NULL DEFAULT 0,
    total_pruned    INTEGER NOT NULL DEFAULT 0,
    error           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_files (
    job_id      TEXT NOT NULL,
    source_key  TEXT NOT NULL,
    state       TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    reused      INTEGER NOT NULL DEFAULT 0,
    pruned      INTEGER NOT NULL DEFAULT 0,
    started_at  REAL,
    finished_at REAL,
    error       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, source_key)
);

CREATE TABLE IF NOT EXISTS run_events (
    job_id  TEXT NOT NULL,
    cursor  INTEGER NOT NULL,
    event   TEXT NOT NULL,
    payload TEXT NOT NULL,
    at      REAL NOT NULL,
    PRIMARY KEY (job_id, cursor)
);

CREATE INDEX IF NOT EXISTS runs_started_at ON runs (started_at DESC);
CREATE INDEX IF NOT EXISTS run_files_key ON run_files (source_key);
"""

# One connection shared across the thread pool.  SQLite serialises writes
# itself, but the Python object is not thread-safe, so a lock guards every use.
_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """Open the history database, creating it and its schema if needed."""
    global _connection

    if _connection is not None:
        return _connection

    settings.run_history_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        settings.run_history_path,
        # Handed between threads by asyncio.to_thread, guarded by _lock.
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    # WAL lets a read (the runs endpoint) proceed while the worker is writing.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(_SCHEMA)
    connection.commit()

    _connection = connection
    return connection


def _write(statement: str, parameters: tuple = ()) -> None:
    """Run one write statement under the connection lock."""
    with _lock:
        connection = _connect()
        connection.execute(statement, parameters)
        connection.commit()


def _read(statement: str, parameters: tuple = ()) -> list[dict[str, Any]]:
    """Run one query under the connection lock, returning plain dicts."""
    with _lock:
        connection = _connect()
        rows = connection.execute(statement, parameters).fetchall()
    return [dict(row) for row in rows]


# --- Lifecycle ------------------------------------------------------------


def _initialise_sync() -> int:
    """Create the schema, close out abandoned runs, and prune old history."""
    with _lock:
        connection = _connect()

        # A run left "running" belonged to a process that no longer exists.
        abandoned = connection.execute(
            "UPDATE runs SET state = ?, finished_at = ?, "
            "error = 'The server stopped before this run finished.' "
            "WHERE state = ?",
            (STATE_ABANDONED, time.time(), STATE_RUNNING),
        ).rowcount

        cutoff = time.time() - RUN_HISTORY_DAYS * 24 * 60 * 60
        connection.execute(
            "DELETE FROM run_events WHERE job_id IN "
            "(SELECT job_id FROM runs WHERE started_at < ?)",
            (cutoff,),
        )
        connection.execute(
            "DELETE FROM run_files WHERE job_id IN "
            "(SELECT job_id FROM runs WHERE started_at < ?)",
            (cutoff,),
        )
        connection.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
        connection.commit()

    return abandoned


async def initialise() -> None:
    """Prepare the history database. Safe to call once at startup."""
    abandoned = await asyncio.to_thread(_initialise_sync)
    if abandoned:
        logger.warning(
            "Marked %d indexing run(s) as abandoned: the server stopped mid-run.",
            abandoned,
        )
    logger.info("Run history ready at %s", settings.run_history_path)


# --- Writes ---------------------------------------------------------------


async def run_started(
    job_id: str,
    keys: list[str],
    embedding_model: str,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    """Record a run beginning."""
    await asyncio.to_thread(
        _write,
        "INSERT OR REPLACE INTO runs "
        "(job_id, state, started_at, embedding_model, chunk_size, chunk_overlap, "
        " queued_keys) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            STATE_RUNNING,
            time.time(),
            embedding_model,
            chunk_size,
            chunk_overlap,
            json.dumps(keys),
        ),
    )


async def run_queue_changed(job_id: str, keys: list[str]) -> None:
    """Record the queue growing while a run is already in flight."""
    await asyncio.to_thread(
        _write,
        "UPDATE runs SET queued_keys = ? WHERE job_id = ?",
        (json.dumps(keys), job_id),
    )


async def run_finished(
    job_id: str,
    state: str,
    indexed: int = 0,
    skipped: int = 0,
    failed: int = 0,
    total_chunks: int = 0,
    total_reused: int = 0,
    total_pruned: int = 0,
    error: str = "",
) -> None:
    """Record a run reaching a terminal state, with its totals."""
    await asyncio.to_thread(
        _write,
        "UPDATE runs SET state = ?, finished_at = ?, indexed = ?, skipped = ?, "
        "failed = ?, total_chunks = ?, total_reused = ?, total_pruned = ?, error = ? "
        "WHERE job_id = ?",
        (
            state,
            time.time(),
            indexed,
            skipped,
            failed,
            total_chunks,
            total_reused,
            total_pruned,
            error,
            job_id,
        ),
    )


async def file_started(job_id: str, source_key: str) -> None:
    """Record the worker picking a file up."""
    await asyncio.to_thread(
        _write,
        "INSERT OR REPLACE INTO run_files "
        "(job_id, source_key, state, started_at) VALUES (?, ?, ?, ?)",
        (job_id, source_key, STATE_RUNNING, time.time()),
    )


async def file_finished(
    job_id: str,
    source_key: str,
    state: str,
    chunk_count: int = 0,
    reused: int = 0,
    pruned: int = 0,
    error: str = "",
) -> None:
    """Record one file's outcome, including embedding work that was skipped."""
    await asyncio.to_thread(
        _write,
        "UPDATE run_files SET state = ?, chunk_count = ?, reused = ?, pruned = ?, "
        "finished_at = ?, error = ? WHERE job_id = ? AND source_key = ?",
        (state, chunk_count, reused, pruned, time.time(), error, job_id, source_key),
    )


async def event_recorded(job_id: str, cursor: int, event: str, payload: str) -> None:
    """Append one streamed event to the run's trace."""
    await asyncio.to_thread(
        _write,
        "INSERT OR REPLACE INTO run_events (job_id, cursor, event, payload, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (job_id, cursor, event, payload, time.time()),
    )


# --- Reads ----------------------------------------------------------------


async def recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent runs, newest first."""
    rows = await asyncio.to_thread(
        _read,
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )
    for row in rows:
        row["queued_keys"] = json.loads(row.get("queued_keys") or "[]")
    return rows


async def get_run(job_id: str) -> Optional[dict[str, Any]]:
    """Return one run's record, or None if history has no such run."""
    rows = await asyncio.to_thread(
        _read, "SELECT * FROM runs WHERE job_id = ?", (job_id,)
    )
    if not rows:
        return None
    rows[0]["queued_keys"] = json.loads(rows[0].get("queued_keys") or "[]")
    return rows[0]


async def run_files(job_id: str) -> list[dict[str, Any]]:
    """Return each file's outcome for one run, in the order it was processed."""
    return await asyncio.to_thread(
        _read,
        "SELECT * FROM run_files WHERE job_id = ? ORDER BY started_at",
        (job_id,),
    )


async def run_events(job_id: str, after: int = -1) -> list[dict[str, Any]]:
    """Return a finished run's event trace past `after`, in order.

    This is what lets a client re-attach to a run whose live buffer has already
    been dropped, or read one back after a restart.
    """
    return await asyncio.to_thread(
        _read,
        "SELECT cursor, event, payload FROM run_events "
        "WHERE job_id = ? AND cursor > ? ORDER BY cursor",
        (job_id, after),
    )
