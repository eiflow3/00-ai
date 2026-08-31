"""Chat traces — what each answered request actually did.

Every chat request is recorded here, judged or not.  That order matters: a
judgement is made minutes or days later, by which time the index may have moved
on, so the evidence has to be captured at answer time or not at all.

Reads join in the verdict rollup from `evaluation_store`, so the traces screen
can show what each exchange scored without loading every judgement on every row.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from app.schemas.evaluation import EvaluationTarget, Verdict
from app.schemas.trace import Trace, TraceChunk, TraceDetail, TracePage, TraceState
from app.services import evaluation_store, trace_db

# Largest page the list endpoint will return, however much is asked for.
MAX_PAGE_SIZE = 200

# Columns written for a trace, in the order the insert binds them.
_TRACE_COLUMNS = (
    "trace_id",
    "created_at",
    "question",
    "answer",
    "provider",
    "model",
    "temperature",
    "system_prompt",
    "use_rag",
    "top_k",
    "score_threshold",
    "embedding_model",
    "total_searched",
    "chunk_count",
    "top_score",
    "state",
    "error_stage",
    "error_message",
    "retrieval_ms",
    "generation_ms",
    "total_ms",
    "input_tokens",
    "output_tokens",
    "total_cost",
)

_INSERT_TRACE = (
    f"INSERT OR REPLACE INTO traces ({', '.join(_TRACE_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _TRACE_COLUMNS)})"
)

_INSERT_CHUNK = (
    "INSERT OR REPLACE INTO trace_chunks "
    "(trace_id, rank, chunk_id, document_id, source_key, score, content, "
    " content_hash, char_count, dropped) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _to_trace(row: dict[str, Any]) -> Trace:
    """Rebuild a Trace from one database row, without its rollup."""
    return Trace(
        trace_id=row["trace_id"],
        created_at=datetime.fromtimestamp(row["created_at"], tz=timezone.utc),
        question=row["question"],
        answer=row["answer"],
        provider=row["provider"],
        model=row["model"],
        temperature=row["temperature"],
        system_prompt=row["system_prompt"],
        use_rag=bool(row["use_rag"]),
        top_k=row["top_k"],
        score_threshold=row["score_threshold"],
        embedding_model=row["embedding_model"],
        total_searched=row["total_searched"],
        chunk_count=row["chunk_count"],
        top_score=row["top_score"],
        state=TraceState(row["state"]),
        error_stage=row["error_stage"],
        error_message=row["error_message"],
        retrieval_ms=row["retrieval_ms"],
        generation_ms=row["generation_ms"],
        total_ms=row["total_ms"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        total_cost=row["total_cost"],
    )


def _to_chunk(row: dict[str, Any]) -> TraceChunk:
    """Rebuild a TraceChunk from one database row."""
    return TraceChunk(
        rank=row["rank"],
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        source_key=row["source_key"],
        score=row["score"],
        content=row["content"],
        content_hash=row["content_hash"],
        char_count=row["char_count"],
        dropped=bool(row["dropped"]),
    )


def _trace_values(trace: Trace) -> tuple:
    """Flatten a Trace into the tuple the insert binds."""
    return (
        trace.trace_id,
        trace.created_at.timestamp(),
        trace.question,
        trace.answer,
        trace.provider,
        trace.model,
        trace.temperature,
        trace.system_prompt,
        int(trace.use_rag),
        trace.top_k,
        trace.score_threshold,
        trace.embedding_model,
        trace.total_searched,
        trace.chunk_count,
        trace.top_score,
        trace.state.value,
        trace.error_stage,
        trace.error_message,
        trace.retrieval_ms,
        trace.generation_ms,
        trace.total_ms,
        trace.input_tokens,
        trace.output_tokens,
        trace.total_cost,
    )


async def save(trace: Trace, chunks: list[TraceChunk]) -> None:
    """Write one finished request and the chunks that grounded it.

    Args:
        trace: The request's record.
        chunks: Every chunk retrieval returned, including ones the score
            threshold dropped before the prompt was built.
    """
    await asyncio.to_thread(trace_db.write, _INSERT_TRACE, _trace_values(trace))
    await asyncio.to_thread(
        trace_db.write_many,
        _INSERT_CHUNK,
        [
            (
                trace.trace_id,
                chunk.rank,
                chunk.chunk_id,
                chunk.document_id,
                chunk.source_key,
                chunk.score,
                chunk.content,
                chunk.content_hash,
                chunk.char_count,
                int(chunk.dropped),
            )
            for chunk in chunks
        ],
    )


def _build_filters(
    model: Optional[str],
    state: Optional[TraceState],
    evaluated: Optional[bool],
    verdict: Optional[Verdict],
    target: Optional[EvaluationTarget],
    source_key: Optional[str],
    search: Optional[str],
) -> tuple[str, list[Any]]:
    """Turn the listing filters into a WHERE clause and its bound values."""
    clauses: list[str] = []
    values: list[Any] = []

    if model:
        clauses.append("t.model = ?")
        values.append(model)

    if state is not None:
        clauses.append("t.state = ?")
        values.append(state.value)

    if search:
        clauses.append("t.question LIKE ?")
        values.append(f"%{search}%")

    if source_key:
        # Which questions pulled a given file — the fastest way to ask whether
        # one document is behind a cluster of bad answers.
        clauses.append(
            "EXISTS (SELECT 1 FROM trace_chunks c "
            "WHERE c.trace_id = t.trace_id AND c.source_key = ?)"
        )
        values.append(source_key)

    # Judgement filters read the live evaluations only: a withdrawn verdict
    # should not keep a trace in a "rated bad" listing.
    judged = "SELECT 1 FROM evaluations e WHERE e.trace_id = t.trace_id AND e.deleted = 0"
    judged_values: list[Any] = []

    if verdict is not None:
        judged += " AND e.verdict = ?"
        judged_values.append(verdict.value)

    if target is not None:
        judged += " AND e.target = ?"
        judged_values.append(target.value)

    if verdict is not None or target is not None:
        clauses.append(f"EXISTS ({judged})")
        values.extend(judged_values)
    elif evaluated is True:
        clauses.append(f"EXISTS ({judged})")
    elif evaluated is False:
        clauses.append(f"NOT EXISTS ({judged})")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, values


async def list_traces(
    limit: int = 50,
    offset: int = 0,
    model: Optional[str] = None,
    state: Optional[TraceState] = None,
    evaluated: Optional[bool] = None,
    verdict: Optional[Verdict] = None,
    target: Optional[EvaluationTarget] = None,
    source_key: Optional[str] = None,
    search: Optional[str] = None,
) -> TracePage:
    """Return one page of traces, newest first.

    Args:
        limit: Page size, capped at MAX_PAGE_SIZE.
        offset: Rows to skip before the page.
        model: Only traces answered by this model.
        state: Only traces that ended this way.
        evaluated: True for judged traces only, False for unjudged only.
        verdict: Only traces carrying a live judgement with this verdict.
        target: Only traces carrying a live judgement of this stage.
        source_key: Only traces that retrieved a chunk from this file.
        search: Substring match on the question.

    Returns:
        The page, with the total number of matching rows for pagination.
    """
    size = max(1, min(limit, MAX_PAGE_SIZE))
    where, values = _build_filters(
        model, state, evaluated, verdict, target, source_key, search
    )

    total = await asyncio.to_thread(
        trace_db.read_value, f"SELECT COUNT(*) FROM traces t {where}", tuple(values)
    )
    rows = await asyncio.to_thread(
        trace_db.read,
        f"SELECT t.* FROM traces t {where} ORDER BY t.created_at DESC LIMIT ? OFFSET ?",
        (*values, size, max(0, offset)),
    )

    traces = [_to_trace(row) for row in rows]
    await _attach_rollups(traces)

    return TracePage(traces=traces, total=int(total), limit=size, offset=max(0, offset))


async def _attach_rollups(traces: list[Trace]) -> None:
    """Fill in each trace's evaluation count and latest verdict per target."""
    if not traces:
        return

    rollups = await evaluation_store.rollup([trace.trace_id for trace in traces])
    for trace in traces:
        summary = rollups.get(trace.trace_id)
        if summary is None:
            continue
        trace.evaluation_count = summary.count
        trace.verdicts = summary.verdicts


async def get_trace(trace_id: str) -> Optional[Trace]:
    """Return one trace with its rollup, or None if there is no such trace."""
    rows = await asyncio.to_thread(
        trace_db.read, "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
    )
    if not rows:
        return None

    trace = _to_trace(rows[0])
    await _attach_rollups([trace])
    return trace


async def get_chunks(trace_id: str) -> list[TraceChunk]:
    """Return the chunks recorded for one trace, best score first."""
    rows = await asyncio.to_thread(
        trace_db.read,
        "SELECT * FROM trace_chunks WHERE trace_id = ? ORDER BY rank",
        (trace_id,),
    )
    return [_to_chunk(row) for row in rows]


async def get_detail(trace_id: str) -> Optional[TraceDetail]:
    """Return one trace with its chunks and every judgement made on it.

    Args:
        trace_id: The trace to load.

    Returns:
        The full record, or None if no such trace exists.
    """
    trace = await get_trace(trace_id)
    if trace is None:
        return None

    return TraceDetail(
        trace=trace,
        chunks=await get_chunks(trace_id),
        # Withdrawn judgements are included: the detail screen is where the
        # history of having changed your mind is worth reading.
        evaluations=await evaluation_store.list_for_trace(trace_id, include_deleted=True),
    )


async def delete_trace(trace_id: str) -> bool:
    """Discard one trace, its chunks and its judgements.

    A hard delete, unlike withdrawing a judgement: this is for a request that
    should never have been recorded, not for one whose verdict changed.

    Args:
        trace_id: The trace to discard.

    Returns:
        Whether a trace was actually removed.
    """
    await asyncio.to_thread(
        trace_db.write, "DELETE FROM trace_chunks WHERE trace_id = ?", (trace_id,)
    )
    await asyncio.to_thread(
        trace_db.write, "DELETE FROM evaluations WHERE trace_id = ?", (trace_id,)
    )
    removed = await asyncio.to_thread(
        trace_db.write, "DELETE FROM traces WHERE trace_id = ?", (trace_id,)
    )
    return bool(removed)


async def distinct_models() -> list[str]:
    """Return every model that has answered, for the listing's filter."""
    rows = await asyncio.to_thread(
        trace_db.read,
        "SELECT DISTINCT model FROM traces WHERE model != '' ORDER BY model",
    )
    return [row["model"] for row in rows]
