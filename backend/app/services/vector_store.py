import os
from pinecone import Pinecone
from app.config import settings

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


def upsert_chunks(vectors: list[dict]):
    """
    Store or update embedded chunks in the Pinecone index.
    
    Args:
        vectors: A list of dictionaries, each containing:
            - "id": str, a unique identifier for the chunk
            - "values": list[float], the vector embedding
            - "metadata": dict, (optional) original text content, document ID, etc.
    """
    # Retrieve the singleton index connection
    index = PineconeManager.get_index()
    index.upsert(vectors=vectors)


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
