"""Assembles the durable record of one chat request as it happens.

The chat endpoint is a stream: by the time an answer is complete, the pieces
that describe it have arrived at four different moments — the request, the
retrieved chunks, the text deltas, and the final usage report.  This collects
them so the router does not have to hold that state itself, and writes the trace
once at the end.

Capturing at answer time is the whole point.  A judgement is made later, by
which time a re-index can have replaced the very chunks the answer was built
from — so what the model was shown has to be written down while it is still true.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.schemas.chat import ChatRequest
from app.schemas.retrieval import RetrievalResult, RetrievedChunk
from app.schemas.trace import Trace, TraceChunk, TraceState
from app.services import provenance, trace_store
from app.services.cost_tracker import CostBreakdown

logger = logging.getLogger(__name__)

# Saves are detached from the request that produced them, so a client that
# disconnects mid-answer still leaves a trace behind. Python keeps only weak
# references to running tasks, so they are held here until they finish.
_pending: set[asyncio.Task] = set()


class TraceRecorder:
    """Collects one request's pieces, then writes them as a single trace."""

    def __init__(self, request: ChatRequest, model: str) -> None:
        """Start recording a request.

        Args:
            request: The chat request as validated.
            model: The model actually resolved for it, which may differ from
                the request's own field when the client sent no override.
        """
        self.trace_id: str = uuid.uuid4().hex
        self._request = request
        self._model = model

        self._created_at = datetime.now(timezone.utc)
        self._started = time.perf_counter()
        self._retrieval_ended: Optional[float] = None
        self._generation_started: Optional[float] = None

        self._chunks: list[TraceChunk] = []
        self._answer: list[str] = []
        self._usage: Optional[CostBreakdown] = None
        self._total_searched = 0
        self._error_stage = ""
        self._error_message = ""
        self._cancelled = False
        self._saved = False

    # --- Collection -------------------------------------------------------

    def record_retrieval(self, result: RetrievalResult) -> None:
        """Capture what retrieval returned, including what it threw away.

        Args:
            result: The retrieval outcome, whether it came from the vector
                store, from client-supplied chunks, or from RAG being off.
        """
        self._retrieval_ended = time.perf_counter()
        self._total_searched = result.total_searched
        self._chunks = [
            _to_trace_chunk(chunk, rank, dropped=False)
            for rank, chunk in enumerate(result.chunks)
        ]
        # Dropped matches continue the ranking rather than restarting it, so
        # `rank` stays a single ordering over everything the search returned.
        offset = len(self._chunks)
        self._chunks.extend(
            _to_trace_chunk(chunk, offset + rank, dropped=True)
            for rank, chunk in enumerate(result.dropped_chunks)
        )

    def append_answer(self, token: str) -> None:
        """Add one streamed delta to the answer being recorded."""
        if self._generation_started is None:
            self._generation_started = time.perf_counter()
        self._answer.append(token)

    def record_error(self, stage: str, message: str) -> None:
        """Note a stage that failed.

        A later failure overwrites an earlier one: retrieval failing is
        survivable and the request goes on, so if generation then fails too,
        that is the one that describes how the request ended.
        """
        self._error_stage = stage
        self._error_message = message

    def record_usage(self, usage: CostBreakdown) -> None:
        """Attach the provider's token counts and the cost derived from them."""
        self._usage = usage

    def record_cancelled(self) -> None:
        """Note that the client went away before the answer finished."""
        self._cancelled = True

    # --- Persistence ------------------------------------------------------

    def persist(self) -> None:
        """Write the trace, without making the caller wait for it.

        Called from the stream's `finally`, which may be running because the
        client disconnected — a context where awaiting is unreliable. Detaching
        it means a trace is never lost to the disconnect that ended the request.
        """
        if self._saved:
            return
        self._saved = True

        task = asyncio.create_task(self._save())
        _pending.add(task)
        task.add_done_callback(_pending.discard)

    async def _save(self) -> None:
        """Build the trace and hand it to the store."""
        try:
            await trace_store.save(self._build(), self._chunks)
        except Exception:
            # A trace that cannot be written must not take the answer with it —
            # the client already has the response, and this is a side record.
            logger.exception("Failed to record chat trace %s", self.trace_id)

    def _build(self) -> Trace:
        """Compose the trace from everything collected so far."""
        now = time.perf_counter()
        kept = [chunk for chunk in self._chunks if not chunk.dropped]

        return Trace(
            trace_id=self.trace_id,
            created_at=self._created_at,
            question=self._request.query,
            answer="".join(self._answer),
            provider=self._request.provider,
            model=self._model,
            temperature=self._request.temperature,
            system_prompt=self._request.system_prompt or "",
            use_rag=self._request.use_rag,
            top_k=self._request.top_k,
            score_threshold=self._request.score_threshold,
            embedding_model=self._request.embedding_model,
            total_searched=self._total_searched,
            chunk_count=len(kept),
            top_score=max((chunk.score for chunk in kept), default=0.0),
            state=self._state(),
            error_stage=self._error_stage,
            error_message=self._error_message,
            retrieval_ms=_elapsed_ms(self._started, self._retrieval_ended),
            generation_ms=_elapsed_ms(self._generation_started, now),
            total_ms=_elapsed_ms(self._started, now),
            input_tokens=self._usage.input_tokens if self._usage else 0,
            output_tokens=self._usage.output_tokens if self._usage else 0,
            total_cost=self._usage.total_cost if self._usage else 0.0,
        )

    def _state(self) -> TraceState:
        """Decide how this request ended.

        Generation failing outranks a disconnect: the provider refused, and
        that is a more useful thing to read later than the fact that the client
        then stopped listening.
        """
        if self._error_stage == "generation":
            return TraceState.FAILED
        if self._cancelled:
            return TraceState.CANCELLED
        return TraceState.COMPLETED


def start(request: ChatRequest, model: str) -> TraceRecorder:
    """Begin recording a chat request.

    Args:
        request: The chat request as validated.
        model: The model resolved for it.

    Returns:
        A recorder holding the trace id to send to the client.
    """
    return TraceRecorder(request, model)


def _to_trace_chunk(chunk: RetrievedChunk, rank: int, dropped: bool) -> TraceChunk:
    """Snapshot one retrieved chunk as it was at answer time."""
    return TraceChunk(
        rank=rank,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        source_key=chunk.source,
        score=chunk.score,
        content=chunk.content,
        # Fingerprinted so a later re-index can be told the id now holds
        # different text — the id alone cannot reveal that.
        content_hash=provenance.content_fingerprint(chunk.content),
        char_count=len(chunk.content),
        dropped=dropped,
    )


def _elapsed_ms(start_at: Optional[float], end_at: Optional[float]) -> int:
    """Milliseconds between two perf-counter readings, zero if either is unset."""
    if start_at is None or end_at is None:
        return 0
    return max(0, round((end_at - start_at) * 1000))
