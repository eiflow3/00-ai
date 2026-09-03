"""Which vector space the application answers from.

Production used to be a *place*: one index, written only by the Sources screen,
and the thing retrieval fell back to when a request named no variant.  That made
"which way of cutting my documents answers best" a question you could measure
but not act on — adopting the winner meant re-embedding the corpus into the
production index and hoping the copy was faithful.

It is a *pointer* instead.  One stored variant id names the namespace that
answers by default, and moving it is instantaneous, reversible, and costs
nothing: the vectors already exist, written by the comparison run that proved
they were better.

Three rules make the pointer safe to move:

  * **Only at something that can answer.**  An empty namespace, a half-embedded
    one, or an id no strategy can reproduce is refused at the moment of
    pointing, because the alternative is discovering it in an ungrounded answer
    an hour later.
  * **Never corrected silently.**  A namespace emptied afterwards — on a
    console, or by a delete — leaves the pointer where it is and reports
    `missing`.  Quietly reverting to the original index would answer from a
    different corpus than the screen names.
  * **Durable.**  It is configuration, like an edited prompt, so it lives in a
    database of its own rather than in memory.  A pointer that reset on restart
    would move every subsequent answer with nothing recording that it had.

One row, read on demand rather than memoised: it is a single local read, and a
cached copy is a way for two processes to disagree about where answers come
from.
"""

import asyncio
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.schemas.chunking import ProductionSpace, VariantState
from app.services import chunk_variants
from app.services.chunk_variants import PRODUCTION_VARIANT, UnknownVariant

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS answering (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    variant_id TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

# The single row. There is only ever one space answering, so the table is
# constrained to one row rather than ordered by a timestamp — a second row here
# would be a bug that reads as a preference.
ROW_ID = 1

# How the original production index reads on screen. Named rather than left
# blank: it is a real choice, and a row labelled "" would look like a fault.
PRODUCTION_LABEL = "Production index"

# One connection shared across the thread pool, guarded like the other SQLite
# stores in this package.
_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


class UnusableSpace(ValueError):
    """Raised when a space cannot answer, so production must not point at it."""


def _connect() -> sqlite3.Connection:
    """Open the pointer database, creating it and its schema if needed."""
    global _connection

    if _connection is not None:
        return _connection

    settings.answering_store_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        settings.answering_store_path,
        # Handed between threads by asyncio.to_thread, guarded by _lock.
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(_SCHEMA)

    _connection = connection
    return connection


def close() -> None:
    """Drop the connection, so the next read opens the file again.

    Used at shutdown, and by tests that need to prove the pointer is on disk
    rather than in this process.
    """
    global _connection

    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None


def _read() -> tuple[str, Optional[datetime]]:
    """Read the stored pointer, or the default when nothing was ever set."""
    with _lock:
        row = (
            _connect()
            .execute("SELECT variant_id, updated_at FROM answering WHERE id = ?", (ROW_ID,))
            .fetchone()
        )

    if row is None:
        # Never configured. Not an error and not "unset" — an installation that
        # has run no experiments answers from where it always did.
        return PRODUCTION_VARIANT, None

    return str(row["variant_id"]), datetime.fromtimestamp(
        float(row["updated_at"]), tz=timezone.utc
    )


def _write(variant: str) -> None:
    """Store the pointer, replacing whatever it was."""
    with _lock:
        connection = _connect()
        connection.execute(
            "INSERT INTO answering (id, variant_id, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET variant_id = excluded.variant_id, "
            "updated_at = excluded.updated_at",
            (ROW_ID, variant, datetime.now(tz=timezone.utc).timestamp()),
        )
        connection.commit()


async def current() -> str:
    """The variant production answers from.

    Returns:
        The variant id, or empty for the original production index.
    """
    variant, _ = await asyncio.to_thread(_read)
    return variant


async def describe() -> ProductionSpace:
    """Report where production answers from, and whether it still can.

    Returns:
        The pointer, with the space's live vector count and file list read back
        from the index rather than remembered.
    """
    variant, updated_at = await asyncio.to_thread(_read)

    if variant == PRODUCTION_VARIANT:
        return await _describe_original(updated_at)

    described = await chunk_variants.describe(variant)

    if described is None:
        # Pointed at a namespace that has since been emptied. Reported as it
        # is; see the module docstring on why this is not corrected here.
        logger.warning("production points at %s, which holds no vectors", variant)
        return ProductionSpace(
            variant_id=variant,
            label=chunk_variants.label_for(chunk_variants.parse(variant)),
            state=VariantState.MISSING,
            updated_at=updated_at,
        )

    return ProductionSpace(
        variant_id=variant,
        label=described.label,
        state=described.state,
        vector_count=described.vector_count,
        source_keys=described.source_keys,
        updated_at=updated_at,
    )


async def _describe_original(updated_at: Optional[datetime]) -> ProductionSpace:
    """Describe the original production index, which has no variant record."""
    from app.services import index_catalog

    documents = await index_catalog.list_indexed_documents()

    return ProductionSpace(
        variant_id=PRODUCTION_VARIANT,
        label=PRODUCTION_LABEL,
        state=VariantState.READY if documents else VariantState.MISSING,
        vector_count=sum(document.chunk_count for document in documents.values()),
        source_keys=sorted(documents),
        updated_at=updated_at,
    )


async def point_at(variant: str) -> ProductionSpace:
    """Move production to a different vector space.

    Args:
        variant: The variant to answer from, or empty for the original index.

    Returns:
        Where production now answers from.

    Raises:
        UnknownVariant: If the id is not one this app can run.
        UnusableSpace: If the space holds nothing, or holds an incomplete copy
            of a file. Both would answer questions with holes in the context,
            and neither would look like a failure on screen.
    """
    # Validated before anything is written, so a rejected move leaves the
    # pointer exactly where it was.
    chunk_variants.space_for(variant)

    state = await _readiness(variant)

    if state is VariantState.MISSING:
        raise UnusableSpace(
            f"{variant or PRODUCTION_LABEL} holds no vectors, so it cannot answer "
            "anything. Index a file into it first."
        )

    if state is VariantState.INTERRUPTED:
        raise UnusableSpace(
            f"{variant or PRODUCTION_LABEL} holds an incomplete copy of at least "
            "one file. Re-index it before answering from it."
        )

    await asyncio.to_thread(_write, variant)
    logger.info("production now answers from %r", variant or PRODUCTION_LABEL)

    return await describe()


async def _readiness(variant: str) -> VariantState:
    """Whether a space can answer, without regard to where production points."""
    if variant == PRODUCTION_VARIANT:
        described = await _describe_original(None)
        return described.state

    described = await chunk_variants.describe(variant)
    return VariantState.MISSING if described is None else described.state


def label_for(variant: str) -> str:
    """Name a space the way it should read on screen.

    Args:
        variant: The variant id, or empty for the original index.

    Returns:
        The label, falling back to the raw id for a variant no strategy can
        reproduce — which can only be one already holding vectors.
    """
    if variant == PRODUCTION_VARIANT:
        return PRODUCTION_LABEL

    try:
        return chunk_variants.label_for(chunk_variants.parse(variant))
    except UnknownVariant:
        return variant
