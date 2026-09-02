"""Registry of the indexing work the pipeline is holding.

Two questions get asked of this module, from opposite directions:

  * the worker asks *what should I do next* — the pending queue;
  * every status row asks *is anything happening to this file* — so a row can
    read "Queued" or "Indexing" rather than silently misreporting what is
    stored as what is happening.

Both are the same state, so they live together.  Keeping them here rather than
in the queue's worker module is what lets `sync_status` read them without
importing the worker that depends on it.

One worker drains the queue, so a file is in exactly one of three positions:
pending, in flight, or absent.  That ordering is also why two runs can no
longer interleave their writes on one file — the situation the earlier
claim-based design existed to prevent — but the in-flight marker is kept
because it is what tells a client which row is actually being embedded.

A queued entry is a file *and how to cut it*, not a file alone.  Embedding one
document under four chunking strategies is four entries, and each has to carry
its own configuration: a run whose geometry was fixed when it started would
silently embed the last three the first one's way, and the comparison they
exist for would be between four copies of the same thing.

The state lives in memory, which is the right scope for a single process: it is
only meaningful while the worker holding it is alive, and it must not survive a
restart that killed that worker.  A second worker or instance would move this
to Redis; nothing else about the design changes.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from app.schemas.chunking import ChunkingConfig

# How long an in-flight marker may stand before it is treated as abandoned.  A
# worker killed without releasing its file would otherwise lock it out forever;
# this is a backstop, not the normal path, which releases in a finally block.
CLAIM_TIMEOUT_SECONDS = 30 * 60


class QueueFull(RuntimeError):
    """Raised when accepting more files would exceed the configured limit.

    Carries a message written for the person who clicked, since it is shown to
    them verbatim.
    """


@dataclass(frozen=True)
class QueuedFile:
    """One file waiting to be embedded, and the terms it will be embedded on."""

    source_key: str

    # Which chunking variant this entry writes to. Empty is production.
    variant: str = ""

    # How to cut this file. Carried per entry rather than per run so four
    # strategies can be queued at once and each is honoured.
    config: ChunkingConfig = field(default_factory=ChunkingConfig)

    # Re-embed even when the index already holds the chunk.
    force: bool = False


# Entries waiting their turn, in the order they were accepted.
_pending: list[QueuedFile] = []

# The entry being embedded right now, and when it was picked up.
_in_flight: Optional[QueuedFile] = None
_in_flight_at: float = 0.0

# Guards both. Accepting a batch has to be atomic across the whole batch, or two
# concurrent requests can both believe they fitted inside the limit.
_lock = asyncio.Lock()


def _expired() -> bool:
    """Whether the in-flight marker is old enough to be treated as abandoned."""
    if _in_flight is None:
        return False
    # monotonic, not wall clock: a system clock change must not expire it.
    return time.monotonic() - _in_flight_at > CLAIM_TIMEOUT_SECONDS


async def enqueue(
    keys: list[str],
    limit: int,
    variant: str = "",
    config: Optional[ChunkingConfig] = None,
    force: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    """Accept files onto the queue, up to the limit.

    Args:
        keys: The files a caller wants indexed, in the order given.
        limit: Most files that may be pending at once.
        variant: Which chunking variant these files are being embedded for.
        config: How to cut them.
        force: Re-embed even what the index already holds.

    Returns:
        Three lists: the keys accepted, the keys already queued or in flight,
        and the keys refused because the queue is full.
    """
    terms = config or ChunkingConfig()

    async with _lock:
        accepted: list[str] = []
        already: list[str] = []
        refused: list[str] = []

        for key in keys:
            # Re-clicking Index on a file that is already waiting *for the same
            # variant* is a no-op, not a second entry — otherwise a queue fills
            # with duplicates and the same file is embedded twice in a row. The
            # same file for a different variant is different work, and queues.
            if _waiting(key, variant):
                already.append(key)
            elif len(_pending) >= limit:
                refused.append(key)
            else:
                _pending.append(QueuedFile(key, variant, terms, force))
                accepted.append(key)

        return accepted, already, refused


def _waiting(source_key: str, variant: str) -> bool:
    """Whether this exact file-and-variant is already queued or in flight."""
    if _in_flight is not None and (
        _in_flight.source_key,
        _in_flight.variant,
    ) == (source_key, variant):
        return True
    return any(
        (entry.source_key, entry.variant) == (source_key, variant)
        for entry in _pending
    )


async def dequeue() -> Optional[QueuedFile]:
    """Take the next entry off the queue and mark it in flight.

    Returns:
        The next entry, or None when the queue is empty.
    """
    global _in_flight, _in_flight_at

    async with _lock:
        if not _pending:
            _in_flight = None
            return None

        _in_flight = _pending.pop(0)
        _in_flight_at = time.monotonic()
        return _in_flight


def release() -> None:
    """Let go of the file in flight.

    Synchronous, and deliberately lock-free: it is called from the worker's
    `finally`, which may already be unwinding a cancellation — and awaiting a
    lock there raises again, leaving a row reading "Indexing" forever. Clearing
    a single reference needs no lock to be correct.
    """
    global _in_flight

    _in_flight = None


def clear() -> list[str]:
    """Drop every pending file and release the one in flight.

    Lock-free for the same reason as `release`: it is the Stop path, and it must
    work while the worker is being torn down.

    Returns:
        The keys that were still waiting, so a caller can report what it
        cancelled rather than only that it cancelled something.
    """
    global _in_flight

    dropped = [entry.source_key for entry in _pending]
    _pending.clear()
    _in_flight = None
    return dropped


def is_indexing(source_key: str) -> bool:
    """Whether this file is being embedded right now.

    Synchronous so it can be called while building a status row, which happens
    per file and must not need the lock.
    """
    return (
        _in_flight is not None
        and _in_flight.source_key == source_key
        and not _expired()
    )


def is_queued(source_key: str) -> bool:
    """Whether this file is waiting its turn, under any variant."""
    return any(entry.source_key == source_key for entry in _pending)


def pending() -> list[str]:
    """Every file waiting, in queue order.

    Keys rather than entries, and a file queued under three variants appears
    three times — which is what a client counting work still to do needs.
    """
    return [entry.source_key for entry in _pending]


def in_flight() -> set[str]:
    """The file being embedded, as a set so callers can test membership."""
    if _in_flight is None or _expired():
        return set()
    return {_in_flight.source_key}


def depth() -> int:
    """How many files are waiting, not counting the one in flight."""
    return len(_pending)
