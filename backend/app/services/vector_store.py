"""Vector store — every call that crosses into Pinecone, and nothing else.

The vendor boundary.  Callers hand it plain dicts and get plain dicts back, so
nothing above this module touches the SDK's types or knows how an index is
provisioned.

**Where a vector lives is an argument, not a global.**  A `VectorSpace` names
an index and a namespace, and every read and write takes one.  That is what
lets the same document be embedded several ways at once: each way writes into
its own namespace, and a query issued against one namespace physically cannot
see another's vectors — the isolation is Pinecone's, not a metadata filter we
remember to apply.  Omitting the argument means the production space, so the
pipeline behaves exactly as it did before namespaces existed.
"""

import threading
from dataclasses import dataclass
from typing import Any, Optional

from pinecone import Pinecone, ServerlessSpec
from pinecone.exceptions import NotFoundException, PineconeApiException

from app.config import settings
from app.services.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_MODEL_METADATA_KEY,
    embedding_dimensions,
)

# Similarity metric for the index.  Cosine is the right pairing for OpenAI's
# embeddings, which are normalised — it is also what the retrieval service's
# 0-1 score bound assumes.
INDEX_METRIC = "cosine"

# Where a newly created serverless index lives.  Free-tier Pinecone only
# offers aws/us-east-1, so this is not really a choice today.
INDEX_CLOUD = "aws"
INDEX_REGION = "us-east-1"

# Pinecone rejects an upsert batch larger than this, and very large batches
# time out well before the limit, so writes are chunked.
UPSERT_BATCH_SIZE = 100

# A fetch request passes ids in the query string, so batches stay modest.
FETCH_BATCH_SIZE = 100

# Pinecone caps how many ids a single delete call accepts.
DELETE_BATCH_SIZE = 1000


@dataclass(frozen=True)
class VectorSpace:
    """Which index and namespace a read or write applies to.

    Both fields default to empty, which means "the configured production
    index, default namespace" — the only place vectors lived before this
    existed. A caller that does not care keeps not caring.
    """

    # Empty means the index named in settings.
    index_name: str = ""

    # Empty means Pinecone's default namespace.
    namespace: str = ""

    @property
    def index(self) -> str:
        """The index this space resolves to."""
        return self.index_name or settings.pinecone_index_name

    @property
    def label(self) -> str:
        """How this space reads in a log line."""
        return f"{self.index}/{self.namespace}" if self.namespace else self.index


# The space the pipeline writes to unless told otherwise.
PRODUCTION = VectorSpace()


def _resolve(space: Optional[VectorSpace]) -> VectorSpace:
    """Fall back to the production space when a caller named none."""
    return space or PRODUCTION


class PineconeManager:
    """
    Singleton manager for the Pinecone client to ensure we only
    initialize the connection once across the entire application lifecycle.

    Index handles are cached per index name rather than singly: resolving one
    costs a round trip to look up its host, and that answer cannot change while
    the process lives.

    Every method here can be reached from a worker thread — the SDK is
    synchronous, so each call is handed to one — and several run at once
    whenever a request reads two spaces in parallel. So construction is locked,
    and the instance is published only once it is fully built: a half-built
    singleton left visible to a second thread is a crash that happens under
    concurrency and never in a test written for one thread.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                # Re-checked inside the lock: another thread may have finished
                # building it while this one waited.
                if cls._instance is None:
                    instance = super(PineconeManager, cls).__new__(cls)
                    # Initialize the connection lazily
                    # This avoids crashing on app startup if env vars are missing
                    instance.pc = Pinecone(api_key=settings.pinecone_api_key)
                    # Handles are built on first use, not here: resolving one
                    # requires the index to exist, and on a fresh account it
                    # does not until ensure_index() has run.
                    instance.indexes = {}
                    # Handles for read-only probes, which must never provision.
                    instance.probe_indexes = {}
                    # Published last, so no thread can observe it half-built.
                    cls._instance = instance
        return cls._instance

    @classmethod
    def get_index(cls, index_name: str = ""):
        """Return an index handle, provisioning the index if it is missing.

        Args:
            index_name: Which index. Empty means the configured production one.

        Returns:
            The cached handle for that index.
        """
        name = index_name or settings.pinecone_index_name
        instance = cls()
        with cls._lock:
            if name not in instance.indexes:
                ensure_index(name)
                instance.indexes[name] = instance.pc.Index(name)
            return instance.indexes[name]

    @classmethod
    def get_probe_index(cls, index_name: str = ""):
        """Return an index handle that never provisions a missing index.

        `get_index` creates the index when it is absent, which is correct
        before a write and wrong for a read: a status check has to be able to
        report "nothing indexed yet" without bringing an index into existence
        as a side effect of being asked.

        Cached like the other handle. Rebuilding it per call cost a round trip
        to resolve the index host, for an answer that cannot change while the
        process lives.

        Raises:
            Whatever the SDK raises when the index does not exist. The caller
            decides whether that is an error or simply "no index yet".
        """
        name = index_name or settings.pinecone_index_name
        instance = cls()
        with cls._lock:
            if name not in instance.probe_indexes:
                instance.probe_indexes[name] = instance.pc.Index(name)
            return instance.probe_indexes[name]

    @classmethod
    def get_client(cls):
        """Returns the active Pinecone control-plane client."""
        return cls().pc


def ensure_index(index_name: str = "", embedding_model: str = "") -> None:
    """Create the named index if it does not exist yet.

    The dimension is derived from the embedding model rather than configured
    separately, which removes the most damaging setup mistake available here:
    an index whose width does not match the vectors we are about to write.
    A mismatch is not rejected loudly by the index — it just fails every
    upsert, or worse, succeeds against a stale index of the wrong shape.

    Safe to call repeatedly; an index that already exists is left untouched.

    Args:
        index_name: Which index to create. Empty means the production one.
        embedding_model: The model whose width the index must match. Empty
            means the configured default.
    """
    client = PineconeManager.get_client()
    name = index_name or settings.pinecone_index_name

    if client.has_index(name):
        return

    try:
        client.create_index(
            name=name,
            dimension=embedding_dimensions(embedding_model or settings.embedding_model),
            metric=INDEX_METRIC,
            spec=ServerlessSpec(cloud=INDEX_CLOUD, region=INDEX_REGION),
            # The default timeout polls until the index is ready to accept
            # traffic; returning earlier would fail the very first upsert.
        )
    except PineconeApiException as exc:
        # Two concurrent requests can both find the index missing. Losing that
        # race is not an error — the index we wanted now exists.
        if getattr(exc, "status", None) != 409:
            raise


def upsert_chunks(
    vectors: list[dict],
    embedding_model: str | None = None,
    space: Optional[VectorSpace] = None,
):
    """
    Store or update embedded chunks in the vector index.

    Every vector is stamped with the embedding model that produced it, so a
    later query embedded with a different model can be detected instead of
    quietly returning meaningless similarity scores.

    Args:
        vectors: A list of dictionaries, each containing:
            - "id": str, a unique identifier for the chunk
            - "values": list[float], the vector embedding
            - "metadata": dict, (optional) original text content, document ID, etc.
        embedding_model: The model that produced these vectors. Defaults to the
            configured model; a value already present in a vector's metadata is
            left alone, so callers can backfill mixed-model batches.
        space: Where to write. Defaults to the production space.
    """
    model = embedding_model or DEFAULT_EMBEDDING_MODEL
    target = _resolve(space)

    # Stamp the model into each vector's metadata without mutating the caller's
    # dicts — the caller may reuse them for logging or retries.
    stamped = []
    for vector in vectors:
        metadata = dict(vector.get("metadata") or {})
        metadata.setdefault(EMBEDDING_MODEL_METADATA_KEY, model)
        stamped.append({**vector, "metadata": metadata})

    index = PineconeManager.get_index(target.index_name)

    # A whole document can exceed Pinecone's per-request limit, so write in
    # batches rather than letting a large file fail the entire upsert.
    for start in range(0, len(stamped), UPSERT_BATCH_SIZE):
        index.upsert(
            vectors=stamped[start : start + UPSERT_BATCH_SIZE],
            namespace=target.namespace,
        )


def query_similar(
    query_embedding: list[float],
    top_k: int = 5,
    space: Optional[VectorSpace] = None,
) -> list[dict]:
    """
    Search for vectors similar to the provided query embedding.

    Args:
        query_embedding: The vector representation of the user's query.
        top_k: The number of closest matches to return.
        space: Where to search. Defaults to the production space. A query never
            crosses out of the namespace it is issued against, which is what
            keeps two chunking strategies' vectors from being scored together.

    Returns:
        The closest matches as plain dicts, each carrying its similarity score
        and metadata. Normalised here so callers never touch the SDK's types.
    """
    target = _resolve(space)

    try:
        index = PineconeManager.get_probe_index(target.index_name)
    except NotFoundException:
        # No index means no context. Reported as an empty result so the caller
        # answers ungrounded and says so, rather than failing the request from
        # inside a stream that has already started.
        return []

    result = index.query(
        vector=query_embedding,
        top_k=top_k,
        include_metadata=True,
        namespace=target.namespace,
    )
    return [to_plain_dict(match) for match in (getattr(result, "matches", None) or [])]


def list_vector_ids(prefix: str, space: Optional[VectorSpace] = None) -> list[str]:
    """List every vector id beginning with `prefix`.

    Vector ids are built so that one source file's chunks share a prefix (see
    app.services.provenance), which makes this the way to enumerate a single
    document's vectors — and the way to delete them, since Pinecone serverless
    offers no delete-by-metadata-filter.

    Args:
        prefix: The vector id prefix to match.
        space: Where to look. Defaults to the production space.

    Returns:
        Every matching vector id, in the order the index returns them.
    """
    target = _resolve(space)

    # A probe rather than the provisioning handle: this is a read, and a read
    # that brings an index into existence as a side effect of being asked is a
    # surprise nobody wants — including the one that would resurrect an index
    # somebody had just deleted.
    try:
        index = PineconeManager.get_probe_index(target.index_name)

        ids: list[str] = []
        # .list() pages internally and yields one batch per iteration. Depending
        # on the SDK version a batch holds plain ids or ListItem objects, so
        # normalise to strings rather than letting the difference leak out.
        for batch in index.list(prefix=prefix, namespace=target.namespace):
            ids.extend(str(getattr(item, "id", item)) for item in batch)
        return ids
    except NotFoundException:
        # An index that does not exist holds no vectors. A valid state on a
        # fresh account, and after an index is retired.
        return []


def to_plain_dict(record) -> dict:
    """Normalise one Pinecone record into a plain dict.

    The SDK returns Vector and ScoredVector objects, which are not mappings —
    `dict(record)` raises on them. Normalising here, at the vendor boundary,
    keeps every caller free of the SDK's types.
    """
    if isinstance(record, dict):
        return record
    if hasattr(record, "to_dict"):
        return record.to_dict()
    # Last resort for an SDK shape we have not seen: read the fields we use.
    return {
        "id": getattr(record, "id", ""),
        "score": getattr(record, "score", None),
        "metadata": getattr(record, "metadata", None) or {},
    }


def fetch_vectors(
    ids: list[str], space: Optional[VectorSpace] = None
) -> dict[str, dict]:
    """Fetch vectors by id, returning their metadata.

    Args:
        ids: The vector ids to fetch.
        space: Where to read. Defaults to the production space.

    Returns:
        A mapping of vector id to its record. Ids that no longer exist are
        simply absent rather than raising.
    """
    if not ids:
        return {}

    target = _resolve(space)

    try:
        index = PineconeManager.get_probe_index(target.index_name)
    except NotFoundException:
        # Nothing to read from an index that is not there.
        return {}

    records: dict[str, dict] = {}
    for start in range(0, len(ids), FETCH_BATCH_SIZE):
        response = index.fetch(
            ids=ids[start : start + FETCH_BATCH_SIZE], namespace=target.namespace
        )
        # The SDK returns objects that behave like dicts; normalise to plain
        # dicts so nothing downstream depends on the vendor's shape.
        vectors = getattr(response, "vectors", None) or {}
        for vector_id, record in vectors.items():
            records[vector_id] = to_plain_dict(record)

    return records


def delete_vectors(ids: list[str], space: Optional[VectorSpace] = None) -> int:
    """Delete vectors by id.

    Args:
        ids: The vector ids to remove.
        space: Where to delete from. Defaults to the production space.

    Returns:
        How many ids were submitted for deletion.
    """
    if not ids:
        return 0

    target = _resolve(space)

    try:
        # A probe here too: creating an index in order to delete from it would
        # leave behind exactly what the caller was trying to get rid of.
        index = PineconeManager.get_probe_index(target.index_name)
    except NotFoundException:
        return 0

    for start in range(0, len(ids), DELETE_BATCH_SIZE):
        index.delete(
            ids=ids[start : start + DELETE_BATCH_SIZE], namespace=target.namespace
        )

    return len(ids)


def delete_namespace(space: VectorSpace) -> None:
    """Delete every vector in a namespace, and the namespace with them.

    The one operation that has no id list behind it: dropping a whole
    experiment. Pinecone treats a namespace as existing only while it holds
    vectors, so this leaves no empty shell behind for a listing to report.

    Args:
        space: The index and namespace to drop. Refuses the default namespace,
            which would empty the whole index rather than one experiment.

    Raises:
        ValueError: If the space names no namespace.
    """
    if not space.namespace:
        raise ValueError(
            "delete_namespace refuses an empty namespace: that would delete "
            "every vector in the index rather than one experiment's."
        )

    try:
        index = PineconeManager.get_probe_index(space.index_name)
        index.delete(delete_all=True, namespace=space.namespace)
    except NotFoundException:
        # Already gone — namespace or index — which is the state the caller
        # asked for. Provisioning an index in order to empty it would not be.
        pass


def index_stats(index_name: str = "") -> dict:
    """Return an index's current statistics, or empty if it does not exist.

    Unlike the other helpers this does not provision a missing index — it is a
    read-only probe, and reporting "no index yet" is a valid answer.

    This sits on the read path of every `GET /sources`, which is why it is
    written the way it is. It used to call `has_index()` first so a missing
    index returned empty rather than raising — but `NotFoundException` below
    already answers that question, from a call we have to make anyway. The
    guard was a second round trip to learn what the first one reports for
    free, and it cost more than the call it guarded.

    Args:
        index_name: Which index to describe. Empty means the production one.
    """
    try:
        return dict(PineconeManager.get_probe_index(index_name).describe_index_stats())
    except NotFoundException:
        # No index yet, which is a valid state on a fresh account rather than a
        # failure. The caller reads an empty result as "cannot tell".
        return {}


def namespace_stats(index_name: str = "") -> dict[str, dict[str, Any]]:
    """Return what each namespace in an index holds.

    The listing an experiment view is built from: which namespaces exist and
    how many vectors are in each. Read from the index itself rather than from a
    table we keep, so it cannot claim an experiment that was deleted from the
    console, or miss one written by another process.

    Args:
        index_name: Which index to describe. Empty means the production one.

    Returns:
        A mapping of namespace name to its stats, empty when the index does not
        exist yet. The default namespace comes back under the empty string.
    """
    stats = index_stats(index_name)
    namespaces = stats.get("namespaces") or {}
    return {str(name): dict(detail) for name, detail in namespaces.items()}
