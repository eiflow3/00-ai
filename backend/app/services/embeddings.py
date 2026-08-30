"""Embedding service — turns text into vectors using OpenAI's embedding models.

Used by the Retrieval phase to embed the user's query with the *same* model
that produced the stored chunk vectors.  Query and chunk vectors must come
from the same model, otherwise similarity scores are meaningless.
"""

from openai import AsyncOpenAI

from app.config import settings

# Default embedding model — must match the model used during the Data phase.
# Defined in Settings because it describes the index's contents; see config.py.
DEFAULT_EMBEDDING_MODEL = settings.embedding_model

# Metadata key under which each stored vector records the model that produced
# it.  Written on upsert, checked on retrieval — defined here so both sides
# can't drift apart on the spelling.
EMBEDDING_MODEL_METADATA_KEY = "embedding_model"

# Output width of each embedding model.  The vector index must be created at
# exactly this dimension, so it is derived from the model rather than
# configured separately — a mismatch fails every upsert against the index.
EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# Fallback width for a model we have no entry for. Matches the two 1536-wide
# models above, which is the common case, but see embedding_dimensions().
FALLBACK_EMBEDDING_DIMENSIONS = 1536

# Reuse a single async client across requests so connections are pooled.
_client = AsyncOpenAI(api_key=settings.openai_api_key)


async def embed_query(text: str, model: str = DEFAULT_EMBEDDING_MODEL) -> list[float]:
    """Embed a single query string and return its dense vector.

    Args:
        text: The raw query text to embed.
        model: The embedding model name (must match the stored vectors').

    Returns:
        The embedding vector as a list of floats.
    """
    response = await _client.embeddings.create(model=model, input=text)
    # A single input yields a single embedding at index 0.
    return response.data[0].embedding


async def embed_texts(
    texts: list[str], model: str = DEFAULT_EMBEDDING_MODEL
) -> list[list[float]]:
    """Embed a batch of texts in one API call.

    Batching is significantly cheaper and faster than one call per text,
    which matters when embedding a whole document's chunks.

    Args:
        texts: The list of text strings to embed.
        model: The embedding model name.

    Returns:
        Vectors in the same order as the input texts.
    """
    response = await _client.embeddings.create(model=model, input=texts)
    # OpenAI returns results out of order in rare cases; sort by index to be safe.
    return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]


def embedding_dimensions(model: str = DEFAULT_EMBEDDING_MODEL) -> int:
    """Return the vector width produced by an embedding model.

    Args:
        model: The embedding model name.

    Returns:
        The number of dimensions in that model's output vectors. An unknown
        model falls back to the most common width rather than failing — the
        index will reject a genuine mismatch on the first upsert, which is a
        clearer error than one raised here before anything is attempted.
    """
    return EMBEDDING_DIMENSIONS.get(model, FALLBACK_EMBEDDING_DIMENSIONS)
