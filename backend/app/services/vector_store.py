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


class PineconeManager:
    """
    Singleton manager for the Pinecone client to ensure we only
    initialize the connection once across the entire application lifecycle.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PineconeManager, cls).__new__(cls)
            # Initialize the connection lazily
            # This avoids crashing on app startup if env vars are missing
            cls._instance.pc = Pinecone(api_key=settings.pinecone_api_key)
            # The index handle is built on first use, not here: resolving it
            # requires the index to exist, and on a fresh account it does not
            # until ensure_index() has run.
            cls._instance.index = None
        return cls._instance

    @classmethod
    def get_index(cls):
        """Returns the active Pinecone index instance, provisioning if needed."""
        instance = cls()
        if instance.index is None:
            ensure_index()
            instance.index = instance.pc.Index(settings.pinecone_index_name)
        return instance.index

    @classmethod
    def get_client(cls):
        """Returns the active Pinecone control-plane client."""
        return cls().pc


def ensure_index() -> None:
    """Create the configured index if it does not exist yet.

    The dimension is derived from the embedding model rather than configured
    separately, which removes the most damaging setup mistake available here:
    an index whose width does not match the vectors we are about to write.
    A mismatch is not rejected loudly by the index — it just fails every
    upsert, or worse, succeeds against a stale index of the wrong shape.

    Safe to call repeatedly; an index that already exists is left untouched.
    """
    client = PineconeManager.get_client()

    if client.has_index(settings.pinecone_index_name):
        return

    try:
        client.create_index(
            name=settings.pinecone_index_name,
            dimension=embedding_dimensions(settings.embedding_model),
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


def upsert_chunks(vectors: list[dict], embedding_model: str | None = None):
    """
    Store or update embedded chunks in the Pinecone index.

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
    """
    model = embedding_model or DEFAULT_EMBEDDING_MODEL

    # Stamp the model into each vector's metadata without mutating the caller's
    # dicts — the caller may reuse them for logging or retries.
    stamped = []
    for vector in vectors:
        metadata = dict(vector.get("metadata") or {})
        metadata.setdefault(EMBEDDING_MODEL_METADATA_KEY, model)
        stamped.append({**vector, "metadata": metadata})

    # Retrieve the singleton index connection
    index = PineconeManager.get_index()

    # A whole document can exceed Pinecone's per-request limit, so write in
    # batches rather than letting a large file fail the entire upsert.
    for start in range(0, len(stamped), UPSERT_BATCH_SIZE):
        index.upsert(vectors=stamped[start : start + UPSERT_BATCH_SIZE])


def query_similar(query_embedding: list[float], top_k: int = 5) -> list[dict]:
    """
    Search the Pinecone index for vectors similar to the provided query embedding.

    Args:
        query_embedding: The vector representation of the user's query.
        top_k: The number of closest matches to return.

    Returns:
        The closest matches as plain dicts, each carrying its similarity score
        and metadata. Normalised here so callers never touch the SDK's types.
    """
    # Retrieve the singleton index connection
    index = PineconeManager.get_index()
    result = index.query(
        vector=query_embedding,
        top_k=top_k,
        include_metadata=True
    )
    return [to_plain_dict(match) for match in (getattr(result, "matches", None) or [])]


def list_vector_ids(prefix: str) -> list[str]:
    """List every vector id beginning with `prefix`.

    Vector ids are built so that one source file's chunks share a prefix (see
    app.services.provenance), which makes this the way to enumerate a single
    document's vectors — and the way to delete them, since Pinecone serverless
    offers no delete-by-metadata-filter.

    Args:
        prefix: The vector id prefix to match.

    Returns:
        Every matching vector id, in the order the index returns them.
    """
    index = PineconeManager.get_index()

    ids: list[str] = []
    # .list() pages internally and yields one batch per iteration. Depending on
    # the SDK version a batch holds plain ids or ListItem objects, so normalise
    # to strings rather than letting the difference leak to callers.
    for batch in index.list(prefix=prefix):
        ids.extend(str(getattr(item, "id", item)) for item in batch)
    return ids


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


def fetch_vectors(ids: list[str]) -> dict[str, dict]:
    """Fetch vectors by id, returning their metadata.

    Args:
        ids: The vector ids to fetch.

    Returns:
        A mapping of vector id to its record. Ids that no longer exist are
        simply absent rather than raising.
    """
    if not ids:
        return {}

    index = PineconeManager.get_index()

    records: dict[str, dict] = {}
    for start in range(0, len(ids), FETCH_BATCH_SIZE):
        response = index.fetch(ids=ids[start : start + FETCH_BATCH_SIZE])
        # The SDK returns objects that behave like dicts; normalise to plain
        # dicts so nothing downstream depends on the vendor's shape.
        vectors = getattr(response, "vectors", None) or {}
        for vector_id, record in vectors.items():
            records[vector_id] = to_plain_dict(record)

    return records


def delete_vectors(ids: list[str]) -> int:
    """Delete vectors by id.

    Args:
        ids: The vector ids to remove.

    Returns:
        How many ids were submitted for deletion.
    """
    if not ids:
        return 0

    index = PineconeManager.get_index()

    for start in range(0, len(ids), DELETE_BATCH_SIZE):
        index.delete(ids=ids[start : start + DELETE_BATCH_SIZE])

    return len(ids)


def index_stats() -> dict:
    """Return the index's current statistics, or empty if it does not exist.

    Unlike the other helpers this does not provision a missing index — it is a
    read-only probe, and reporting "no index yet" is a valid answer.
    """
    client = PineconeManager.get_client()
    if not client.has_index(settings.pinecone_index_name):
        return {}

    try:
        return dict(client.Index(settings.pinecone_index_name).describe_index_stats())
    except NotFoundException:
        return {}
