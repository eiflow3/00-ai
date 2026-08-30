import os
from pinecone import Pinecone
from app.config import settings
from app.services.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_MODEL_METADATA_KEY,
)

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
            cls._instance.index = cls._instance.pc.Index(settings.pinecone_index_name)
        return cls._instance

    @classmethod
    def get_index(cls):
        """Returns the active Pinecone index instance."""
        return cls().index


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
    index.upsert(vectors=stamped)


def query_similar(query_embedding: list[float], top_k: int = 5):
    """
    Search the Pinecone index for vectors similar to the provided query embedding.
    
    Args:
        query_embedding: The vector representation of the user's query.
        top_k: The number of closest matches to return.
        
    Returns:
        A list of matches with their similarity scores and metadata.
    """
    # Retrieve the singleton index connection
    index = PineconeManager.get_index()
    result = index.query(
        vector=query_embedding,
        top_k=top_k,
        include_metadata=True
    )
    return result
