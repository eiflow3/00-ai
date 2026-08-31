"""Cache service — a small key/value store with a Redis backend.

Deliberately generic: it knows about strings, counters and expiry, and nothing
at all about sources, vectors or object keys.  What is worth caching and when
it stops being true lives in app.services.source_cache; this module only has
to store bytes and give them back.

Two backends stand behind the same functions:

  * **Redis**, when a connection is configured — survives a reload, and is
    shared once more than one worker is running.
  * **an in-process dict**, otherwise — correct for a single worker, and the
    reason the app still starts on a laptop with no Redis on it.

The fallback is not only for a missing URL.  A cache is an optimisation, so a
Redis that goes away mid-flight must degrade rather than take the endpoints
down with it: a failed call drops to the in-process backend and schedules a
re-probe, and a Redis that comes back is picked up again on its own.

Nothing here raises. A cache that cannot answer reports a miss, and the caller
does the work it would have done anyway.
"""

import asyncio
import logging
import time
from typing import Optional, Protocol

from app.config import settings

logger = logging.getLogger(__name__)

# How long to keep using the in-process backend after Redis fails before
# trying it again.  Long enough that a Redis restart is not hammered, short
# enough that a recovered one is picked up within a page load or two.
REDIS_RETRY_SECONDS = 30.0

# How many entries the in-process backend holds.  The cache is a handful of
# keys per source file, so this is a guard against a pathological bucket
# rather than a tuning knob.
MEMORY_MAX_ENTRIES = 2048

# Seconds a Redis call may take before it is treated as a failure.  A cache
# that is slower than the work it replaces is worse than no cache.
REDIS_TIMEOUT_SECONDS = 2.0


class _Backend(Protocol):
    """What both backends provide. Values are strings; expiry is in seconds."""

    name: str

    async def get(self, key: str) -> Optional[str]: ...

    async def set(self, key: str, value: str, ttl: int) -> None: ...

    async def delete(self, *keys: str) -> None: ...

    async def incr(self, key: str, ttl: int) -> int: ...


class _MemoryBackend:
    """An in-process dict with expiry.

    Expiry is checked on read rather than swept on a timer: there is no
    background task to leak, and an entry nobody asks for costs only the space
    it occupies until the size bound reclaims it.
    """

    name = "memory"

    def __init__(self) -> None:
        # key -> (expires_at monotonic, value)
        self._entries: dict[str, tuple[float, str]] = {}

    def _evict(self) -> None:
        """Drop expired entries, then the oldest, until back under the bound."""
        now = time.monotonic()
        for key, (expires_at, _) in list(self._entries.items()):
            if expires_at <= now:
                del self._entries[key]

        # Insertion order is close enough to age for a bound this size, and
        # avoids carrying a heap around for a cache of a few hundred keys.
        while len(self._entries) > MEMORY_MAX_ENTRIES:
            self._entries.pop(next(iter(self._entries)))

    async def get(self, key: str) -> Optional[str]:
        entry = self._entries.get(key)
        if entry is None:
            return None

        expires_at, value = entry
        if expires_at <= time.monotonic():
            del self._entries[key]
            return None

        return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        self._entries[key] = (time.monotonic() + ttl, value)
        self._evict()

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._entries.pop(key, None)

    async def incr(self, key: str, ttl: int) -> int:
        current = await self.get(key)
        value = int(current) + 1 if current is not None and current.isdigit() else 1
        await self.set(key, str(value), ttl)
        return value


class _RedisBackend:
    """Redis, reached through the asyncio client.

    Every call is bounded by a timeout. Redis is normally sub-millisecond, so
    a call that takes seconds is a network problem, and waiting it out would
    hand the request a latency the cache exists to remove.
    """

    name = "redis"

    def __init__(self, url: str) -> None:
        # Imported here rather than at module scope so the package stays
        # optional: an install without redis still runs on the memory backend.
        from redis.asyncio import Redis

        self._client = Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        )

    async def ping(self) -> None:
        await asyncio.wait_for(self._client.ping(), REDIS_TIMEOUT_SECONDS)

    async def get(self, key: str) -> Optional[str]:
        return await asyncio.wait_for(self._client.get(key), REDIS_TIMEOUT_SECONDS)

    async def set(self, key: str, value: str, ttl: int) -> None:
        await asyncio.wait_for(
            self._client.set(key, value, ex=ttl), REDIS_TIMEOUT_SECONDS
        )

    async def delete(self, *keys: str) -> None:
        if keys:
            await asyncio.wait_for(self._client.delete(*keys), REDIS_TIMEOUT_SECONDS)

    async def incr(self, key: str, ttl: int) -> int:
        # One round trip for both, so a counter can never be left without an
        # expiry by a connection that dropped between the two commands.
        pipeline = self._client.pipeline()
        pipeline.incr(key)
        pipeline.expire(key, ttl)
        incremented, _ = await asyncio.wait_for(
            pipeline.execute(), REDIS_TIMEOUT_SECONDS
        )
        return int(incremented)

    async def close(self) -> None:
        await self._client.aclose()


_memory = _MemoryBackend()

# The Redis backend once it has answered a ping. None means "not connected",
# which is either "never tried" or "failed, waiting to retry".
_redis: Optional[_RedisBackend] = None

# Monotonic time before which Redis is not probed again.
_retry_after: float = 0.0

# Serialises the connection attempt, so a burst of requests on a cold cache
# opens one connection rather than one each.
_connect_lock = asyncio.Lock()


async def _backend() -> _Backend:
    """Return the backend to use for this call.

    Returns:
        Redis when it is configured and reachable, the in-process cache
        otherwise. Never raises — a cache that cannot be reached is a slower
        request, not a failed one.
    """
    global _redis, _retry_after

    if not settings.redis_dsn:
        return _memory

    if _redis is not None:
        return _redis

    if time.monotonic() < _retry_after:
        return _memory

    async with _connect_lock:
        # Another request may have connected while this one waited.
        if _redis is not None:
            return _redis
        if time.monotonic() < _retry_after:
            return _memory

        try:
            candidate = _RedisBackend(settings.redis_dsn)
            await candidate.ping()
        except Exception as exc:
            _retry_after = time.monotonic() + REDIS_RETRY_SECONDS
            logger.warning(
                "Cache falling back to memory; Redis unreachable: %s", exc
            )
            return _memory

        _redis = candidate
        logger.info("Cache using Redis")
        return _redis


def _demote(exc: Exception) -> None:
    """Drop the Redis backend after a failed call and schedule a re-probe."""
    global _redis, _retry_after

    if _redis is None:
        return

    _redis = None
    _retry_after = time.monotonic() + REDIS_RETRY_SECONDS
    logger.warning("Cache falling back to memory; Redis call failed: %s", exc)


async def get(key: str) -> Optional[str]:
    """Read one value.

    Args:
        key: The cache key.

    Returns:
        The stored string, or None when the key is absent, expired, or the
        cache could not be read.
    """
    backend = await _backend()
    try:
        return await backend.get(key)
    except Exception as exc:
        _demote(exc)
        return None


async def set(key: str, value: str, ttl: int) -> None:
    """Store one value under an expiry.

    Args:
        key: The cache key.
        value: The string to store.
        ttl: Seconds before the entry expires.
    """
    backend = await _backend()
    try:
        await backend.set(key, value, ttl)
    except Exception as exc:
        _demote(exc)


async def delete(*keys: str) -> None:
    """Remove keys, ignoring any that are not there.

    Args:
        keys: The cache keys to drop.
    """
    backend = await _backend()
    try:
        await backend.delete(*keys)
    except Exception as exc:
        _demote(exc)


async def incr(key: str, ttl: int) -> int:
    """Increment a counter and return its new value.

    A counter is how cached data is stamped with the generation it was built
    in: bumping it makes every entry carrying the old value read as a miss,
    without having to find and delete those entries.

    Args:
        key: The counter's key.
        ttl: Seconds before the counter itself expires. Should be far longer
            than the entries it guards, or a counter that expired and reset
            could match a payload built under the earlier generation.

    Returns:
        The counter's new value, or 0 when the cache could not be written —
        which reads as a generation nothing matches, so a failure to bump
        invalidates rather than serving stale data.
    """
    backend = await _backend()
    try:
        return await backend.incr(key, ttl)
    except Exception as exc:
        _demote(exc)
        return 0


async def close() -> None:
    """Release the Redis connection, if one is open."""
    global _redis

    client, _redis = _redis, None
    if client is not None:
        try:
            await client.close()
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.warning("Cache close failed: %s", exc)


def backend_name() -> str:
    """Which backend is currently serving, for logs and diagnostics."""
    return _redis.name if _redis is not None else _memory.name
