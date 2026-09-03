"""The Pinecone handle, built from several threads at once.

The SDK is synchronous, so every call to it is handed to a worker thread — and
a single request now reads more than one vector space in parallel, which means
more than one of those threads can be the *first* one.

That found a real bug: the singleton assigned itself to the class before
filling in its attributes, so a second thread arriving in the gap got an object
with no `probe_indexes` on it and the request died with an `AttributeError`.
It never reproduced under one thread, and no amount of re-running the suite
would have found it.

These tests recreate the conditions rather than the interleaving: every thread
is released at the same instant, and building the client is made slow enough to
leave the window wide open. What they assert is the outcome — nobody gets a
broken manager, and everybody gets the same handle — which is what failed
before the constructor was locked.
"""

import threading
import time

import pytest

from app.services import vector_store


# Long enough that a thread arriving while the client is being built is the
# normal case rather than a coincidence, short enough to be free.
BUILD_SECONDS = 0.05

# Enough threads that the window is hit from several directions at once.
THREADS = 8


class _SlowClient:
    """A Pinecone client that takes a moment to construct, as the real one does."""

    def __init__(self) -> None:
        time.sleep(BUILD_SECONDS)

    def Index(self, name: str) -> str:  # noqa: N802 — mirrors the SDK's name
        return f"handle:{name}"


def _race(work) -> tuple[list[object], list[BaseException]]:
    """Run `work` on every thread, released together, and collect both outcomes."""
    start = threading.Barrier(THREADS)
    results: list[object] = []
    failures: list[BaseException] = []

    def run(index: int) -> None:
        start.wait(timeout=5)
        try:
            results.append(work(index))
        except BaseException as exc:  # noqa: BLE001 — the point is to catch any
            failures.append(exc)

    workers = [threading.Thread(target=run, args=(index,)) for index in range(THREADS)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    return results, failures


@pytest.fixture
def racing_manager(monkeypatch):
    """A manager with nothing built yet, and a client that is slow to build."""
    monkeypatch.setattr(vector_store, "Pinecone", lambda api_key: _SlowClient())
    monkeypatch.setattr(vector_store.PineconeManager, "_instance", None)
    monkeypatch.setattr(vector_store, "ensure_index", lambda *args, **kwargs: None)

    yield

    vector_store.PineconeManager._instance = None


def test_the_singleton_is_never_seen_half_built(racing_manager):
    """Whoever loses the race must still get a usable manager, not a wreck."""
    results, failures = _race(
        lambda _: vector_store.PineconeManager.get_probe_index("rag-index")
    )

    assert not failures, failures
    # And every thread got the same handle: a per-thread handle would mean a
    # host lookup per call, which is the cost the cache exists to avoid.
    assert results == ["handle:rag-index"] * THREADS


def test_two_spaces_resolved_at_once_share_one_client(racing_manager):
    """The case that actually broke: two indexes, read in parallel, cold."""
    names = ["rag-index", "rag-chunk-lab"]
    seen, failures = _race(
        lambda index: vector_store.PineconeManager.get_index(names[index % len(names)])
    )

    assert not failures, failures
    assert set(seen) == {"handle:rag-index", "handle:rag-chunk-lab"}
