"""Turns evaluated traces into a JSONL file you can hold.

The database is the store; this is the portable copy — one JSON object per
evaluated exchange, carrying the question, the answer, every chunk that grounded
it, and the judgements made on it.  That is enough for the file to be read,
diffed or replayed long after the index it came from has changed.

Only traces with a live judgement are exported.  A withdrawn verdict leaves the
trace in the database, where the record of having changed your mind is worth
reading, but it does not belong in a dataset that stands for what you think now.
"""

import json
from typing import Any, AsyncIterator

from app.schemas.evaluation import Evaluation
from app.schemas.trace import Trace, TraceChunk
from app.services import evaluation_store, trace_store

# Traces loaded per round trip while streaming. Small enough that a large export
# never holds the whole table in memory, large enough to keep the queries few.
EXPORT_BATCH_SIZE = 50

# Written on every line, so a file read back later can be told which shape it is.
SCHEMA_VERSION = 1


def _chunk_line(chunk: TraceChunk) -> dict[str, Any]:
    """Serialise one chunk as it was when the answer was written."""
    return {
        "rank": chunk.rank,
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "source_key": chunk.source_key,
        "score": round(chunk.score, 6),
        "content": chunk.content,
        "content_hash": chunk.content_hash,
        "char_count": chunk.char_count,
        "dropped": chunk.dropped,
    }


def _evaluation_line(evaluation: Evaluation) -> dict[str, Any]:
    """Serialise one judgement."""
    return {
        "id": evaluation.id,
        "target": evaluation.target.value,
        "verdict": evaluation.verdict.value,
        "tags": evaluation.tags,
        "note": evaluation.note,
        "author": evaluation.author.value,
        "created_at": evaluation.created_at.isoformat(),
    }


def build_line(
    trace: Trace, chunks: list[TraceChunk], evaluations: list[Evaluation]
) -> dict[str, Any]:
    """Compose one export record.

    Grouped by pipeline stage rather than flattened, so the file answers the
    question it exists for — was this a retrieval problem or a generation
    problem — by reading, not by cross-referencing.

    Args:
        trace: The exchange being exported.
        chunks: Every chunk retrieval returned for it.
        evaluations: The live judgements made on it.

    Returns:
        A JSON-serialisable record.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace.trace_id,
        "created_at": trace.created_at.isoformat(),
        "question": trace.question,
        "answer": trace.answer,
        "retrieval": {
            "use_rag": trace.use_rag,
            "embedding_model": trace.embedding_model,
            "top_k": trace.top_k,
            "score_threshold": trace.score_threshold,
            "total_searched": trace.total_searched,
            "chunk_count": trace.chunk_count,
            "top_score": round(trace.top_score, 6),
            "elapsed_ms": trace.retrieval_ms,
            "chunks": [_chunk_line(chunk) for chunk in chunks],
        },
        "generation": {
            "provider": trace.provider,
            "model": trace.model,
            "temperature": trace.temperature,
            "system_prompt": trace.system_prompt,
            "input_tokens": trace.input_tokens,
            "output_tokens": trace.output_tokens,
            "total_cost": round(trace.total_cost, 8),
            "elapsed_ms": trace.generation_ms,
        },
        "outcome": {
            "state": trace.state.value,
            "error_stage": trace.error_stage,
            "error_message": trace.error_message,
        },
        "evaluations": [_evaluation_line(item) for item in evaluations],
    }


async def stream_jsonl() -> AsyncIterator[str]:
    """Yield every evaluated trace as one JSON line, newest first.

    Paged rather than loaded at once, so exporting a long history does not
    build the whole file in memory before the first byte is sent.

    Yields:
        One newline-terminated JSON object per evaluated exchange.
    """
    offset = 0

    while True:
        page = await trace_store.list_traces(
            limit=EXPORT_BATCH_SIZE, offset=offset, evaluated=True
        )
        if not page.traces:
            return

        # Newest first, matching the listing. Reversing per page would only
        # reverse *within* a page, which reads as neither order.
        for trace in page.traces:
            chunks = await trace_store.get_chunks(trace.trace_id)
            evaluations = await evaluation_store.list_for_trace(trace.trace_id)
            line = build_line(trace, chunks, evaluations)
            yield json.dumps(line, ensure_ascii=False) + "\n"

        offset += len(page.traces)
        if offset >= page.total:
            return
