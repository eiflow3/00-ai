# Re-export all schema models for convenient imports.
# Grouped by RAG pipeline stage: Data → Retrieval → Generation.

# --- Shared / utility schemas ---
from app.schemas.common import EchoRequest, EchoResponse, HealthResponse

# --- Data phase schemas ---
from app.schemas.document import Document
from app.schemas.chunk import Chunk
from app.schemas.embedding import Embedding, EmbeddingRequest

# --- Retrieval phase schemas ---
from app.schemas.retrieval import RetrievalQuery, RetrievedChunk, RetrievalResult

# --- Generation phase schemas ---
from app.schemas.generation import GenerationRequest, GenerationResponse

# --- Chat schemas ---
from app.schemas.chat import ChatRequest

# --- Prompt schemas ---
from app.schemas.prompt import Prompt, PromptId, PromptPreview, PromptUpdateRequest

# --- Trace and evaluation schemas ---
from app.schemas.trace import Trace, TraceChunk, TraceDetail, TracePage
from app.schemas.evaluation import Evaluation, EvaluationOptions, EvaluationRequest

__all__ = [
    # Common
    "HealthResponse",
    "EchoRequest",
    "EchoResponse",
    # Data phase
    "Document",
    "Chunk",
    "Embedding",
    "EmbeddingRequest",
    # Retrieval phase
    "RetrievalQuery",
    "RetrievedChunk",
    "RetrievalResult",
    # Generation phase
    "GenerationRequest",
    "GenerationResponse",
    # Chat
    "ChatRequest",
    # Prompts
    "Prompt",
    "PromptId",
    "PromptPreview",
    "PromptUpdateRequest",
    # Traces and evaluations
    "Trace",
    "TraceChunk",
    "TraceDetail",
    "TracePage",
    "Evaluation",
    "EvaluationOptions",
    "EvaluationRequest",
]
