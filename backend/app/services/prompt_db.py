"""The local database behind prompt edits.

Owns the connection and the schema — nothing about what a prompt *means*, which
is `prompt_catalog`'s job, or which one is in force, which is `prompt_store`'s.

Its own file, deliberately.  The other two databases both prune: run history at
thirty days, and unjudged traces with it.  An edited prompt is configuration —
it is what every subsequent answer is written under, and a retention sweep that
quietly restored a default would change the pipeline's behaviour with nothing
recording that it had.

Only defaults are ever *absent* here.  A row exists for a prompt precisely when
someone has overridden it, so resetting one is a delete rather than a write.

``sqlite3`` is synchronous, so every call is handed to a worker thread like the
other blocking clients in this package.
"""

import asyncio
import logging
import sqlite3
import threading
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_overrides (
    id         TEXT PRIMARY KEY,
    template   TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

# One connection shared across the thread pool.  SQLite serialises writes
# itself, but the Python object is not thread-safe, so a lock guards every use.
_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """Open the prompt database, creating it and its schema if needed."""
    global _connection

    if _connection is not None:
        return _connection

    settings.prompt_store_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        settings.prompt_store_path,
        # Handed between threads by asyncio.to_thread, guarded by _lock.
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
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


def read(statement: str, parameters: tuple = ()) -> list[dict[str, Any]]:
    """Run one query under the connection lock, returning plain dicts."""
    with _lock:
        connection = _connect()
        rows = connection.execute(statement, parameters).fetchall()
    return [dict(row) for row in rows]


async def initialise() -> None:
    """Prepare the prompt database. Safe to call once at startup."""
    rows = await asyncio.to_thread(read, "SELECT COUNT(*) AS n FROM prompt_overrides")
    edited = rows[0]["n"] if rows else 0
    # Worth a line at startup: an edited prompt changes every answer that
    # follows, and it should not take reading the UI to discover that.
    logger.info(
        "Prompt store ready at %s (%d prompt(s) overridden)",
        settings.prompt_store_path,
        edited,
    )
