"""Retrieval service — the R in RAG.

Runs the full retrieval phase for a query:
    query text -> embedding -> vector similarity search -> ranked chunks

Sits between the vector store (raw Pinecone matches) and the chat route,
which injects the results into the prompt and streams them to the client.
"""

import asyncio

from app.schemas.retrieval import RetrievedChunk, RetrievalResult
from app.services.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_MODEL_METADATA_KEY,
    embed_query,
)
from app.services.provenance import (
    METADATA_CONTENT,
    METADATA_DOCUMENT_ID,
    METADATA_SOURCE_KEY,
)
from app.services.vector_store import query_similar


class EmbeddingModelMismatch(RuntimeError):
    """Raised when stored vectors came from a different model than the query.

    Similarity scores across two embedding spaces are meaningless, so this
    fails the retrieval outright rather than handing the LLM plausible-looking
    but semantically unrelated chunks.
    """


def _assert_matching_model(matches: list[dict], query_model: str) -> None:
    """Fail loudly if any match was embedded with a different model.

    Vectors written before the model was stamped carry no such metadata; those
    are left alone so an existing index keeps working until it is re-indexed.
    """
    found = {
        str(model)
        for match in matches
        for model in [(match.get("metadata") or {}).get(EMBEDDING_MODEL_METADATA_KEY)]
        if model
    }

    mismatched = found - {query_model}
    if mismatched:
        raise EmbeddingModelMismatch(
            f"Query was embedded with {query_model!r} but the index returned "
            f"vectors from {sorted(mismatched)!r}. Re-index, or query with the "
            f"model the vectors were built from."
        )


def _match_field(metadata: dict, *names: str, default: str = "") -> str:
    """Return the first present, non-empty metadata field from `names`.

    The upsert side may label the same field differently depending on how the
    vector was written, so we accept each known spelling rather than silently
    returning "" and losing the link back to the source file.
    """
    for name in names:
        value = metadata.get(name)
        if value:
            return str(value)
    return default


def _to_retrieved_chunk(match: dict) -> RetrievedChunk:
    """Convert one Pinecone match into a RetrievedChunk.

    The vector store already normalised the SDK's object into a plain dict, so
    this only has to map fields into our own schema.
    """
    metadata = match.get("metadata") or {}

    # Cosine scores can land marginally outside [0, 1] (float error, or a
    # dot-product index), so clamp before the schema's 0–1 bound rejects them.
    score = max(0.0, min(1.0, float(match.get("score") or 0.0)))

    return RetrievedChunk(
        chunk_id=str(match.get("id", "")),
        document_id=_match_field(metadata, METADATA_DOCUMENT_ID, "doc_id"),
        content=_match_field(metadata, METADATA_CONTENT, "text"),
        score=score,
        # The data embedding pipeline writes the object key as `source_key`
        # (see app.services.provenance) — read it first so a citation traces
        # straight back to the file in storage. The rest are older spellings
        # from hand-loaded vectors.
        source=_match_field(metadata, METADATA_SOURCE_KEY, "source", "url", "filename"),
    )


async def retrieve(
    query: str,
    top_k: int = 5,
    score_threshold: float = 0.0,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> RetrievalResult:
    """Embed the query, search the vector store, and return ranked chunks.

    Args:
        query: The user's natural-language question.
        top_k: Maximum number of chunks to return.
        score_threshold: Drop matches scoring below this value.
        embedding_model: Model used to embed the query.

    Returns:
        A RetrievalResult with chunks ordered by descending similarity score.
    """
    # 1. Embed the query with the same model used for the stored chunks.
    vector = await embed_query(query, model=embedding_model)

    # 2. Search Pinecone. The client is synchronous, so run it off the event
    #    loop — otherwise the network call blocks every other request.
    matches = await asyncio.to_thread(query_similar, vector, top_k)

    # 3. Refuse to score across embedding spaces — a model mismatch here would
    #    otherwise surface as confident-looking but unrelated chunks.
    _assert_matching_model(matches, embedding_model)

    # 4. Normalise the vendor response into our schema.
    chunks = [_to_retrieved_chunk(match) for match in matches]

    # 5. Split on the threshold, then order each side best-first. The weak
    #    matches are returned rather than discarded so a caller recording what
    #    happened can show that the right passage was there and just missed.
    chunks.sort(key=lambda c: c.score, reverse=True)
    kept = [c for c in chunks if c.score >= score_threshold]
    dropped = [c for c in chunks if c.score < score_threshold]

    return RetrievalResult(
        query=query,
        chunks=kept,
        dropped_chunks=dropped,
        total_searched=len(matches),
    )
