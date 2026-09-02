"""Runs a chunking comparison off the request, and streams what it is doing.

Scoring four variants against twenty questions is eighty retrievals and, when
answers are generated, eighty model calls.  That is minutes of work, so it
cannot be the response to the request that asked for it — a reloaded tab would
throw it away, exactly as it did for indexing and for golden-set drafting
before both were moved off the request.  The shape here is the same one:

    POST starts a run  ->  a worker task scores  ->  clients open a stream onto
                                                     whatever it is doing

There is deliberately no history table behind this.  A score describes an index
at a moment: what a variant retrieved today says nothing about what it will
retrieve after the file is re-indexed, and a stored table of old scores would
invite exactly that comparison. The run is held in memory while it matters and
is then gone; re-running it is the honest way to have it again.

Variants are scored one after another rather than at once.  They share an
embedding endpoint and a model, so running them concurrently would mostly
queue at the provider, and it would make the per-variant timings — which are
part of what is being compared — meaningless.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from pydantic import BaseModel

from app.schemas.variant_score import (
    RowScore,
    ScoreEnqueueResponse,
    ScoreErrorEvent,
    ScoreErrorEventData,
    ScoreProgressEvent,
    ScoreProgressEventData,
    ScoreRun,
    ScoreRunState,
    ScoreStartedEvent,
    ScoreStartedEventData,
    ScoreSummaryEvent,
    ScoreSummaryEventData,
    ScoreVariantEvent,
    VariantScore,
    VariantScoreRequest,
)
from app.services import (
    chunk_variants,
    golden_export,
    golden_store,
    object_store,
    prompt_store,
    text_extraction,
    variant_scorer,
)
from app.services.chunk_sections import section_spans
from app.services.llm import catalog as model_catalog
from app.services.llm.factory import get_adapter

logger = logging.getLogger(__name__)

# How long a finished run stays readable before it is dropped. Long enough to
# reload the tab and still see the table, short enough that nothing treats a
# stale measurement as current.
JOB_RETENTION_SECONDS = 3600

# Backstop on the subscriber wait, so a stream re-checks rather than hanging if
# a notification is ever missed.
SUBSCRIBE_POLL_SECONDS = 15.0

_TERMINAL_STATES = frozenset(
    {ScoreRunState.COMPLETED, ScoreRunState.FAILED, ScoreRunState.CANCELLED}
)


class ScoringRefused(ValueError):
    """Raised when a run cannot start, with a reason written for a person."""


class _Job:
    """One comparison run: its event buffer and its running totals."""

    def __init__(self, request: VariantScoreRequest, variants: list[str], rows: int) -> None:
        self.job_id = uuid.uuid4().hex[:16]
        self.set_id = request.set_id
        self.variants = variants
        self.state = ScoreRunState.RUNNING

        # Events emitted so far, each under an increasing cursor. This is what
        # a re-attaching client replays.
        self.events: list[tuple[int, BaseModel]] = []
        self.cursor = -1

        # Wakes every subscriber when an event lands. A condition rather than
        # an event so two tabs watching one run cannot starve each other.
        self.updated = asyncio.Condition()

        self.task: Optional[asyncio.Task] = None
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: Optional[datetime] = None
        self.finished_monotonic: Optional[float] = None
        self.error = ""

        self.completed = 0
        self.total = rows * len(variants)

    @property
    def terminal(self) -> bool:
        """Whether the run has stopped, for whatever reason."""
        return self.state in _TERMINAL_STATES

    async def emit(self, event: BaseModel) -> int:
        """Buffer an event and wake every subscriber."""
        async with self.updated:
            self.cursor += 1
            cursor = self.cursor
            self.events.append((cursor, event))
            self.updated.notify_all()
        return cursor


# Every job still held in memory, finished ones included until they age out.
_jobs: dict[str, _Job] = {}

# Guards starting a run, so two clicks cannot open two runs at once.
_lock = asyncio.Lock()


def _default_provider() -> str:
    """The first provider this deployment can actually use."""
    entries = model_catalog.list_models()
    for entry in entries:
        if entry["available"]:
            return entry["provider"]
    return entries[0]["provider"] if entries else ""


async def _variants_for(source_key: str, requested: list[str]) -> list[str]:
    """Decide which variants a run covers.

    Args:
        source_key: The file the golden set was drafted from.
        requested: Variants the caller named, or empty for "everything".

    Returns:
        The variants to score, in a stable order.

    Raises:
        ScoringRefused: When nothing holds the file, so there is nothing to
            compare — which is a state to explain, not an empty table to show.
    """
    if requested:
        return list(dict.fromkeys(requested))

    # Only variants that actually hold this file. Scoring one that does not
    # would report a recall of zero and read as a terrible strategy rather than
    # as a file nobody indexed that way.
    holding = [
        variant.variant_id
        for variant in await chunk_variants.list_variants()
        if source_key in variant.source_keys
    ]

    if not holding:
        raise ScoringRefused(
            f"No chunking variant holds {source_key!r} yet. Index it under at "
            f"least two variants first, then score them against each other."
        )

    return holding


async def enqueue(request: VariantScoreRequest) -> ScoreEnqueueResponse:
    """Start a comparison run and return the id to follow it by.

    Args:
        request: Which golden set, which variants, and whether to generate.

    Returns:
        The run's job id and what it will do.

    Raises:
        ScoringRefused: When the set has no usable rows, or no variant holds
            the file it was drafted from.
        UnknownGoldenSet: When the set does not exist.
        ValueError: When the provider is unknown.
    """
    detail = await golden_store.get(request.set_id)

    # A row a person dropped is one they rejected as a question; scoring
    # against it would measure the variants on something already disowned.
    rows = golden_export.exportable(detail.rows)
    if not rows:
        raise ScoringRefused(
            f"Golden set {detail.slug!r} has no rows to score with. Accept some "
            f"rows on the Golden Sets tab first."
        )

    variants = await _variants_for(detail.source_key, request.variants)

    provider = request.provider or _default_provider()
    model = request.model or model_catalog.DEFAULT_MODELS.get(provider, "")

    if request.generate:
        # Fail here rather than inside the worker: an unusable provider is the
        # caller's mistake, and a 400 says so where a failed run would not.
        get_adapter(provider)

    async with _lock:
        _drop_expired()
        job = _Job(request, variants, len(rows))
        _jobs[job.job_id] = job
        job.task = asyncio.create_task(
            _worker(job, request, detail.source_key, rows, provider, model),
            name=f"variant-score-{job.job_id}",
        )

    logger.info(
        "Scoring run %s started: %d variant(s) x %d row(s) on %s",
        job.job_id,
        len(variants),
        len(rows),
        detail.source_key,
    )

    return ScoreEnqueueResponse(job_id=job.job_id, variants=variants, rows=len(rows))


async def _worker(
    job: _Job,
    request: VariantScoreRequest,
    source_key: str,
    rows: list,
    provider: str,
    model: str,
) -> None:
    """Score every variant in turn, reporting each question as it lands."""
    started = time.monotonic()
    results: list[VariantScore] = []

    try:
        data = await object_store.get_object(source_key)
        text = text_extraction.extract_text(source_key, data)

        # Sections are located once and shared: the document is the same for
        # every variant, and it is the yardstick they are all measured by.
        spans = section_spans(text)
        prompts = await prompt_store.active()

        await job.emit(
            ScoreStartedEvent(
                data=ScoreStartedEventData(
                    job_id=job.job_id,
                    set_id=job.set_id,
                    source_key=source_key,
                    variants=job.variants,
                    rows=len(rows),
                    generating=request.generate,
                )
            )
        )

        for variant in job.variants:
            async def report(score: RowScore, variant_id: str = variant) -> None:
                """Put each question on the wire as it finishes."""
                job.completed += 1
                await job.emit(
                    ScoreProgressEvent(
                        data=ScoreProgressEventData(
                            variant_id=variant_id,
                            completed=job.completed,
                            total=job.total,
                            score=score,
                        )
                    )
                )

            try:
                result = await variant_scorer.score_variant(
                    variant=variant,
                    rows=rows,
                    text=text,
                    spans=spans,
                    top_k=request.top_k,
                    generate=request.generate,
                    provider=provider,
                    model=model,
                    prompts=prompts,
                    on_row=report,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One variant failing outright still leaves the others worth
                # comparing, so it is reported and the run carries on.
                logger.exception("Scoring run %s: %s failed", job.job_id, variant)
                await job.emit(
                    ScoreErrorEvent(
                        data=ScoreErrorEventData(variant_id=variant, message=str(exc))
                    )
                )
                continue

            results.append(result)
            await job.emit(ScoreVariantEvent(data=result))

        job.state = ScoreRunState.COMPLETED

    except asyncio.CancelledError:
        job.state = ScoreRunState.CANCELLED
        job.error = "Stopped before every variant was scored."
        logger.info("Scoring run %s cancelled", job.job_id)
        await _close(job, results, started)
        raise

    except Exception as exc:
        job.state = ScoreRunState.FAILED
        job.error = str(exc)
        logger.exception("Scoring run %s failed: %s", job.job_id, exc)
        await job.emit(ScoreErrorEvent(data=ScoreErrorEventData(message=str(exc))))
        await _close(job, results, started)
        return

    await _close(job, results, started)


async def _close(job: _Job, results: list[VariantScore], started: float) -> None:
    """Emit the ranking and mark the run finished."""
    job.finished_at = datetime.now(timezone.utc)
    job.finished_monotonic = time.monotonic()

    # Ranked on retrieval recall, which is the measure chunking is actually
    # responsible for; correctness breaks a tie, because two variants that
    # retrieve equally well are separated by what the model could do with it.
    ranked = sorted(results, key=lambda score: (score.recall, score.correct), reverse=True)

    await job.emit(
        ScoreSummaryEvent(
            data=ScoreSummaryEventData(
                scores=ranked,
                winner=ranked[0].variant_id if ranked else "",
                duration_seconds=time.monotonic() - started,
            )
        )
    )


def _drop_expired() -> None:
    """Forget finished runs once their numbers are too old to act on."""
    now = time.monotonic()
    for job_id, job in list(_jobs.items()):
        if job.finished_monotonic and now - job.finished_monotonic > JOB_RETENTION_SECONDS:
            _jobs.pop(job_id, None)


def get(job_id: str) -> Optional[ScoreRun]:
    """One run, as a client reopening the stream finds it."""
    job = _jobs.get(job_id)
    if job is None:
        return None

    return ScoreRun(
        job_id=job.job_id,
        set_id=job.set_id,
        state=job.state,
        variants=job.variants,
        completed=job.completed,
        total=job.total,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        last_cursor=max(job.cursor, 0),
    )


async def subscribe(job_id: str, after: int = -1) -> AsyncIterator[tuple[int, BaseModel]]:
    """Follow one run's events, replaying whatever came after `after`.

    Args:
        job_id: The run to follow.
        after: Cursor already seen. Everything past it is replayed, then the
            stream follows live.

    Yields:
        Each event with its cursor.
    """
    job = _jobs.get(job_id)
    if job is None:
        # Aged out, or from before a restart. There is nothing durable behind a
        # score, so the client's recourse is to run it again.
        return

    while True:
        async with job.updated:
            pending = [item for item in job.events if item[0] > after]

        for cursor, event in pending:
            yield cursor, event
            after = cursor

        if job.terminal:
            # The summary can land between the snapshot and here, and still has
            # to go out before the stream closes.
            async with job.updated:
                remaining = [item for item in job.events if item[0] > after]
            for cursor, event in remaining:
                yield cursor, event
            return

        async with job.updated:
            try:
                await asyncio.wait_for(job.updated.wait(), timeout=SUBSCRIBE_POLL_SECONDS)
            except asyncio.TimeoutError:
                # Backstop only; loop round and re-check rather than hanging.
                pass


async def cancel(job_id: str) -> bool:
    """Stop a run in flight.

    Args:
        job_id: The run to stop.

    Returns:
        True when a running job was stopped.
    """
    async with _lock:
        job = _jobs.get(job_id)
        if job is None or job.terminal or job.task is None:
            return False
        job.task.cancel()
        return True
