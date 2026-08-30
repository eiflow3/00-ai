"""Registry of files currently being indexed.

Two indexing runs on the same file are not merely wasteful — they interleave
badly. One run can upsert eight chunks while another, working from a shorter
version of the same file, prunes everything past chunk three. The result is an
index that matches neither version.

So a run claims the keys it is about to process, and anything already claimed is
left alone. The claim lives in memory, which is the right scope for a single
process: the state is only meaningful while the run holding it is alive, and it
should not survive a restart that killed that run. Running more than one worker
or instance would move this to Redis — nothing else about the design changes.
"""

import asyncio
import time

# How long a claim may stand before it is treated as abandoned.  A run that
# died without releasing its keys would otherwise lock them out forever; this
# is a backstop, not the normal path, which releases in a finally block.
CLAIM_TIMEOUT_SECONDS = 30 * 60

# Key -> monotonic timestamp the claim was made.
_claims: dict[str, float] = {}

# Guards the claim table.  Claiming has to be atomic across the whole batch,
# otherwise two runs starting together can both believe they won the same key.
_lock = asyncio.Lock()


def _expired(claimed_at: float, now: float) -> bool:
    """Whether a claim is old enough to be treated as abandoned."""
    return now - claimed_at > CLAIM_TIMEOUT_SECONDS


def _live_claims(now: float) -> dict[str, float]:
    """Drop abandoned claims and return what remains."""
    for key, claimed_at in list(_claims.items()):
        if _expired(claimed_at, now):
            del _claims[key]
    return _claims


async def claim(keys: list[str]) -> tuple[list[str], list[str]]:
    """Take exclusive hold of the keys that are free.

    Args:
        keys: The keys a run intends to process.

    Returns:
        The keys this caller now holds, and the keys another run already holds,
        both in the order they were given.
    """
    # monotonic, not wall clock: a system clock change must not expire claims.
    now = time.monotonic()

    async with _lock:
        live = _live_claims(now)

        claimed: list[str] = []
        busy: list[str] = []

        for key in keys:
            if key in live:
                busy.append(key)
            else:
                live[key] = now
                claimed.append(key)

    return claimed, busy


async def release(keys: list[str]) -> None:
    """Give up the claims held on these keys.

    Args:
        keys: Keys previously returned as claimed. Unknown keys are ignored, so
            a release can be called unconditionally from a finally block.
    """
    async with _lock:
        for key in keys:
            _claims.pop(key, None)


def is_indexing(source_key: str) -> bool:
    """Whether a run currently holds this key.

    Synchronous so it can be called while building a status row, which happens
    per file and must not need the lock.
    """
    claimed_at = _claims.get(source_key)
    return claimed_at is not None and not _expired(claimed_at, time.monotonic())


def in_flight() -> set[str]:
    """Every key currently held by a run."""
    now = time.monotonic()
    return {key for key, at in _claims.items() if not _expired(at, now)}
