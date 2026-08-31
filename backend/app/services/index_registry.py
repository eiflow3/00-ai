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

The state lives in memory, which is the right scope for a single process: it is
only meaningful while the worker holding it is alive, and it must not survive a
restart that killed that worker.  A second worker or instance would move this
to Redis; nothing else about the design changes.
"""

import asyncio
import time
from typing import Optional

# How long an in-flight marker may stand before it is treated as abandoned.  A
# worker killed without releasing its file would otherwise lock it out forever;
# this is a backstop, not the normal path, which releases in a finally block.
CLAIM_TIMEOUT_SECONDS = 30 * 60


class QueueFull(RuntimeError):
    """Raised when accepting more files would exceed the configured limit.

    Carries a message written for the person who clicked, since it is shown to
    them verbatim.
    """


# Files waiting their turn, in the order they were accepted.
_pending: list[str] = []

# The file being embedded right now, and when it was picked up.
_in_flight: Optional[str] = None
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


async def enqueue(keys: list[str], limit: int) -> tuple[list[str], list[str], list[str]]:
    """Accept files onto the queue, up to the limit.

    Args:
        keys: The files a caller wants indexed, in the order given.
        limit: Most files that may be pending at once.

    Returns:
        Three lists: the keys accepted, the keys already queued or in flight,
        and the keys refused because the queue is full.
    """
    async with _lock:
        accepted: list[str] = []
        already: list[str] = []
        refused: list[str] = []

        for key in keys:
            # Re-clicking Index on a file that is already waiting is a no-op,
            # not a second entry — otherwise a queue fills with duplicates and
            # the same file is embedded twice in a row.
            if key in _pending or key == _in_flight:
                already.append(key)
            elif len(_pending) >= limit:
                refused.append(key)
            else:
                _pending.append(key)
                accepted.append(key)

        return accepted, already, refused


async def dequeue() -> Optional[str]:
    """Take the next file off the queue and mark it in flight.

    Returns:
        The next key, or None when the queue is empty.
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

    dropped = list(_pending)
    _pending.clear()
    _in_flight = None
    return dropped


def is_indexing(source_key: str) -> bool:
    """Whether this file is being embedded right now.

    Synchronous so it can be called while building a status row, which happens
    per file and must not need the lock.
    """
    return _in_flight == source_key and not _expired()


def is_queued(source_key: str) -> bool:
    """Whether this file is waiting its turn."""
    return source_key in _pending


def pending() -> list[str]:
    """Every file waiting, in queue order."""
    return list(_pending)


def in_flight() -> set[str]:
    """The file being embedded, as a set so callers can test membership."""
    return set() if _in_flight is None or _expired() else {_in_flight}


def depth() -> int:
    """How many files are waiting, not counting the one in flight."""
    return len(_pending)
