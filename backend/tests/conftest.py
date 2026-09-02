"""Test harness: the pipeline, with its three vendors replaced by fakes.

The backend had no tests, and the reason it was awkward to give it any is that
every interesting path crosses a network boundary — R2 for the bytes, OpenAI for
the vectors, Pinecone for everything after that.  A suite that needs all three
is a suite nobody runs, and one that mocks at the call site proves only that the
mock was called.

So the fakes here sit at the *vendor boundary* and nowhere else:

  * `FakeIndex` stands in for a Pinecone index handle, so every line of
    `services/vector_store` runs for real — the batching, the id stamping, and
    crucially the namespace argument, which is the whole basis of variant
    isolation.
  * `fake_vector` embeds text as a normalised bag of hashed words, so cosine
    similarity still means term overlap.  A retrieval test can therefore assert
    that the *right* chunk came back, not merely that some chunk did.
  * `FakeObjects` holds bytes in a dict.

Everything above those three is the real code, including the queue, the run
history and the chunking registry.

One wrinkle worth knowing: several services import vendor functions by name
(`from ...object_store import get_object`), so patching the source module alone
would miss them.  Each binding is therefore patched explicitly, and the length
of that list is the honest cost of those direct imports rather than something to
hide.
"""

import asyncio
import os
import re
import zlib
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REPORT = BACKEND.parent / "data" / "01-meridian-fy2025-annual-report.txt"

# Settings requires provider credentials at import time.  On a developer machine
# they are configured already and are never spent, because every call that would
# spend them is faked below.  Where they are not configured — a fresh clone, or
# CI — fill in placeholders so importing the app does not fail for want of a key
# it will not use.  Placeholders are only ever *added*: a configured environment
# is left exactly as it is, which is what keeps the `--live` tests working.
CONFIGURED = BACKEND / ".env"

if not CONFIGURED.exists():
    for _name in ("OPENAI_API_KEY", "PINECONE_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.setdefault(_name, f"test-{_name.lower()}")

# Imported after the environment is settled, which is why these are not at the
# top of the file.
from app.config import settings  # noqa: E402
from app.schemas.source import SourceObject  # noqa: E402
from app.services import (  # noqa: E402
    chunk_preview,
    deletion,
    embeddings as embeddings_service,
    index_queue,
    index_registry,
    ingestion,
    object_store,
    retrieval,
    run_store,
    sync_status,
    uploads,
    vector_store,
)

# Width of a fake embedding.  Nothing here needs a real embedding space, only
# one where similar text scores higher than dissimilar text — but too narrow a
# space collides unrelated words into the same bucket and makes the ranking
# arbitrary, so it is wide enough for a term-overlap assertion to mean
# something.
DIMENSIONS = 1024

# How many ids a fake listing returns per batch, mirroring the SDK's paging.
LIST_BATCH = 50

_WORD = re.compile(r"[a-z0-9][a-z0-9.,]*")


# --- The fakes --------------------------------------------------------------


def fake_vector(text: str) -> list[float]:
    """Embed text as a normalised bag of hashed words.

    Deterministic across processes — `zlib.crc32` rather than `hash`, which is
    salted per run and would make a similarity assertion pass or fail depending
    on the day.

    Args:
        text: The text to embed.

    Returns:
        A unit-length vector, so a dot product is a cosine similarity.
    """
    counts = [0.0] * DIMENSIONS
    for word in _WORD.findall(text.lower()):
        counts[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0

    length = sum(value * value for value in counts) ** 0.5
    if length == 0:
        # An all-punctuation chunk. Any unit vector will do, but it must not be
        # the zero vector — cosine similarity would be undefined rather than
        # merely low.
        return [1.0] + [0.0] * (DIMENSIONS - 1)

    return [value / length for value in counts]


class _Record:
    """One stored vector, shaped like the SDK's record objects."""

    def __init__(self, vector_id: str, values: list[float], metadata: dict) -> None:
        self.id = vector_id
        self.values = values
        self.metadata = metadata

    def to_dict(self) -> dict:
        return {"id": self.id, "values": self.values, "metadata": self.metadata}


class _Match(_Record):
    """A scored record, as a query returns it."""

    def __init__(self, record: _Record, score: float) -> None:
        super().__init__(record.id, record.values, record.metadata)
        self.score = score

    def to_dict(self) -> dict:
        return {"id": self.id, "score": self.score, "metadata": self.metadata}


class _Fetched:
    """What a fetch returns: an object carrying a `vectors` mapping."""

    def __init__(self, vectors: dict[str, _Record]) -> None:
        self.vectors = vectors


class _Queried:
    """What a query returns: an object carrying `matches`."""

    def __init__(self, matches: list[_Match]) -> None:
        self.matches = matches


class FakeIndex:
    """An in-memory stand-in for a Pinecone index handle.

    Namespace-aware on purpose: it is the only thing in the suite that can prove
    a query does not cross out of the namespace it was issued against, which is
    the promise the whole comparison feature rests on.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        # namespace -> vector id -> record
        self.namespaces: dict[str, dict[str, _Record]] = {}

    def _space(self, namespace: str) -> dict[str, _Record]:
        return self.namespaces.setdefault(namespace or "", {})

    def upsert(self, vectors: list[dict], namespace: str = "") -> None:
        space = self._space(namespace)
        for vector in vectors:
            space[vector["id"]] = _Record(
                vector["id"], list(vector["values"]), dict(vector.get("metadata") or {})
            )

    def query(
        self,
        vector: list[float],
        top_k: int = 5,
        include_metadata: bool = True,
        namespace: str = "",
    ) -> _Queried:
        space = self._space(namespace)

        scored = [
            _Match(record, sum(a * b for a, b in zip(vector, record.values)))
            for record in space.values()
        ]
        scored.sort(key=lambda match: match.score, reverse=True)
        return _Queried(scored[:top_k])

    def list(self, prefix: str = "", namespace: str = "") -> Iterator[list[str]]:
        space = self._space(namespace)
        matching = sorted(key for key in space if key.startswith(prefix))
        for start in range(0, len(matching), LIST_BATCH):
            yield matching[start : start + LIST_BATCH]

    def fetch(self, ids: list[str], namespace: str = "") -> _Fetched:
        space = self._space(namespace)
        return _Fetched({key: space[key] for key in ids if key in space})

    def delete(
        self,
        ids: Optional[list[str]] = None,
        delete_all: bool = False,
        namespace: str = "",
    ) -> None:
        space = self._space(namespace)
        if delete_all:
            space.clear()
            # Pinecone treats a namespace as existing only while it holds
            # vectors, and the variant listing depends on that.
            self.namespaces.pop(namespace or "", None)
            return
        for vector_id in ids or []:
            space.pop(vector_id, None)

    def describe_index_stats(self) -> dict:
        return {
            "namespaces": {
                name: {"vector_count": len(records)}
                for name, records in self.namespaces.items()
                if records
            },
            "total_vector_count": sum(
                len(records) for records in self.namespaces.values()
            ),
        }


class FakeObjects:
    """Object storage as a dict, standing in for R2."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def get_object(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    async def head_object(self, key: str) -> SourceObject:
        if key not in self.objects:
            raise FileNotFoundError(key)

        data = self.objects[key]
        return SourceObject(
            key=key,
            last_modified="2026-01-01T00:00:00Z",
            size=len(data),
            etag=zlib.crc32(data).to_bytes(4, "big").hex(),
        )

    async def list_objects(self, prefix: str = "") -> list[SourceObject]:
        return [
            await self.head_object(key)
            for key in sorted(self.objects)
            if key.startswith(prefix)
        ]

    async def put_object(
        self, key: str, data: bytes, content_type: str = ""
    ) -> SourceObject:
        self.objects[key] = data
        return await self.head_object(key)

    async def delete_object(self, key: str) -> bool:
        return self.objects.pop(key, None) is not None


# --- Options ----------------------------------------------------------------


def pytest_addoption(parser) -> None:
    """Add `--live`, which lets the vendor-facing tests actually run."""
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run tests that hit the real Pinecone, OpenAI and R2.",
    )


def pytest_collection_modifyitems(config, items) -> None:
    """Skip the live tests unless they were asked for.

    They cost money and need credentials, so off is the only sane default — but
    they are worth having, because a fake cannot notice the day a vendor renames
    an argument.
    """
    if config.getoption("--live"):
        return

    skip = pytest.mark.skip(reason="needs --live (spends money, needs credentials)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def report_text() -> str:
    """The Meridian annual report — the corpus every test measures against."""
    return REPORT.read_text(encoding="utf-8")


@pytest.fixture
def report_key() -> str:
    """The object key the report is stored under in the fake bucket."""
    return "01-meridian-fy2025-annual-report.txt"


@pytest.fixture
def objects(monkeypatch, report_text, report_key) -> FakeObjects:
    """Object storage, holding the report.

    Patched into each importer by name as well as at the source, because
    several services do `from ...object_store import get_object`.
    """
    store = FakeObjects()
    store.put(report_key, report_text.encode("utf-8"))

    # The client itself is barred first. Patching the functions alone was not
    # enough once: `services/deletion` imports `delete_object` by name, that
    # binding was missed, and a test deleted a real object out of the real
    # bucket. A fake at the *client* seam turns a missed binding into a failed
    # test instead of a destroyed file.
    monkeypatch.setattr(
        object_store.ObjectStoreManager,
        "get_client",
        staticmethod(lambda: pytest.fail("object storage was called for real")),
    )

    monkeypatch.setattr(object_store, "get_object", store.get_object)
    monkeypatch.setattr(object_store, "head_object", store.head_object)
    monkeypatch.setattr(object_store, "list_objects", store.list_objects)
    monkeypatch.setattr(object_store, "put_object", store.put_object)
    monkeypatch.setattr(object_store, "delete_object", store.delete_object)

    # And every module that imported one of them by name.
    monkeypatch.setattr(ingestion, "get_object", store.get_object)
    monkeypatch.setattr(ingestion, "head_object", store.head_object)
    monkeypatch.setattr(sync_status, "head_object", store.head_object)
    monkeypatch.setattr(sync_status, "list_objects", store.list_objects)
    monkeypatch.setattr(deletion, "delete_object", store.delete_object)
    monkeypatch.setattr(uploads, "head_object", store.head_object)
    monkeypatch.setattr(uploads, "put_object", store.put_object)

    return store


@pytest.fixture
def embeddings(monkeypatch) -> None:
    """Deterministic embeddings, patched into everything that embeds."""

    async def embed_texts(texts: list[str], model: str = "") -> list[list[float]]:
        return [fake_vector(text) for text in texts]

    async def embed_query(text: str, model: str = "") -> list[float]:
        return fake_vector(text)

    # Same reasoning as object storage: bar the client, then patch the
    # bindings. A missed binding then costs a failed test rather than money.
    class _Barred:
        def __getattr__(self, name: str):
            pytest.fail("the embedding API was called for real")

    monkeypatch.setattr(embeddings_service, "_client", _Barred())

    monkeypatch.setattr(embeddings_service, "embed_texts", embed_texts)
    monkeypatch.setattr(embeddings_service, "embed_query", embed_query)
    monkeypatch.setattr(ingestion, "embed_texts", embed_texts)
    monkeypatch.setattr(retrieval, "embed_query", embed_query)


@pytest.fixture
def indexes(monkeypatch) -> dict[str, FakeIndex]:
    """Every index this test touches, keyed by name.

    Patched at `PineconeManager`, which is the one seam that leaves the whole of
    `services/vector_store` running for real — including the namespace argument
    the isolation tests are about.
    """
    built: dict[str, FakeIndex] = {}

    def resolve(index_name: str = "") -> FakeIndex:
        name = index_name or settings.pinecone_index_name
        return built.setdefault(name, FakeIndex(name))

    monkeypatch.setattr(
        vector_store.PineconeManager, "get_index", staticmethod(resolve)
    )
    monkeypatch.setattr(
        vector_store.PineconeManager, "get_probe_index", staticmethod(resolve)
    )
    # Nothing should reach the control plane once the handles are faked. If
    # something does, fail loudly rather than opening a connection.
    monkeypatch.setattr(
        vector_store.PineconeManager,
        "get_client",
        staticmethod(lambda: pytest.fail("the Pinecone control plane was called")),
    )

    return built


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    """Give each test its own queue, run history and cache.

    All three are module-level state, which is right for one process serving one
    person and wrong for a suite — a file left in `_pending` by one test would
    be picked up by the next one's worker.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(run_store, "_connection", None)

    # The cached index read is a correctness hazard here rather than a saving: a
    # test asserts on what the index holds immediately after writing it.
    monkeypatch.setattr(settings, "cache_enabled", False)

    index_registry.clear()
    monkeypatch.setattr(index_queue, "_active", None)
    monkeypatch.setattr(index_queue, "_jobs", {})

    yield

    index_registry.clear()
    if run_store._connection is not None:
        run_store._connection.close()
        run_store._connection = None


@pytest.fixture
def lab(objects, embeddings, indexes) -> dict[str, FakeIndex]:
    """The whole pipeline with all three vendors faked. What most tests want."""
    return indexes


# --- Helpers ----------------------------------------------------------------


@pytest.fixture
def index_variant():
    """A helper that runs one indexing request to completion.

    A fixture rather than an importable function, so a test module does not
    have to be a package to reach it.
    """
    return _index_variant


async def _index_variant(source_key: str, variant: str = "", **fields: Any):
    """Run one indexing request to completion.

    The queue is the real one, so the work happens on a worker task rather than
    inside `enqueue`. Awaiting that task is deterministic, where polling for a
    terminal state would be a race with a timeout attached.

    Args:
        source_key: The file to index.
        variant: Which variant to write to. Empty is production.
        **fields: Anything else `IndexRequest` accepts.

    Returns:
        The finished run, as the runs listing would report it.
    """
    from app.schemas.ingestion import IndexRequest

    response = await index_queue.enqueue(
        IndexRequest(keys=[source_key], variant=variant, **fields)
    )

    job = index_queue._jobs[response.job_id]
    if job.task is not None:
        await asyncio.shield(job.task)

    return index_queue.get(response.job_id)
