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
from app.services.vector_store import query_similar


class EmbeddingModelMismatch(RuntimeError):
    """Raised when stored vectors came from a different model than the query.

    Similarity scores across two embedding spaces are meaningless, so this
    fails the retrieval outright rather than handing the LLM plausible-looking
    but semantically unrelated chunks.
    """


def _assert_matching_model(matches: list, query_model: str) -> None:
    """Fail loudly if any match was embedded with a different model.

    Vectors written before the model was stamped carry no such metadata; those
    are left alone so an existing index keeps working until it is re-indexed.
    """
    found = {
        str(model)
        for match in matches
        for model in [
            ((dict(match) if not isinstance(match, dict) else match).get("metadata") or {}).get(
                EMBEDDING_MODEL_METADATA_KEY
            )
        ]
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

    The upsert side may label chunk text as "content" or "text" depending on
    the ingestion path, so we accept either rather than silently returning "".
    """
    for name in names:
        value = metadata.get(name)
        if value:
            return str(value)
    return default


def _to_retrieved_chunk(match) -> RetrievedChunk:
    """Convert one raw Pinecone match into a RetrievedChunk.

    Pinecone returns objects that behave like dicts; we normalise them into
    our own schema so nothing downstream depends on the vendor's shape.
    """
    # Pinecone match objects support both attribute and key access.
    raw = dict(match) if not isinstance(match, dict) else match
    metadata = raw.get("metadata") or {}

    # Cosine scores can land marginally outside [0, 1] (float error, or a
    # dot-product index), so clamp before the schema's 0–1 bound rejects them.
    score = max(0.0, min(1.0, float(raw.get("score") or 0.0)))

    return RetrievedChunk(
        chunk_id=str(raw.get("id", "")),
        document_id=_match_field(metadata, "document_id", "doc_id"),
        content=_match_field(metadata, "content", "text"),
        score=score,
        source=_match_field(metadata, "source", "url", "filename"),
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
    response = await asyncio.to_thread(query_similar, vector, top_k)

    # 3. Refuse to score across embedding spaces — a model mismatch here would
    #    otherwise surface as confident-looking but unrelated chunks.
    matches = (response or {}).get("matches") or []
    _assert_matching_model(matches, embedding_model)

    # 4. Normalise the vendor response into our schema.
    chunks = [_to_retrieved_chunk(match) for match in matches]

    # 5. Drop weak matches, then order best-first.
    chunks = [c for c in chunks if c.score >= score_threshold]
    chunks.sort(key=lambda c: c.score, reverse=True)

    return RetrievalResult(
        query=query,
        chunks=chunks,
        total_searched=len(matches),
    )
