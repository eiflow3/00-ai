"""Runs a generation off the request, and streams what it is doing.

Drafting a golden set is a dozen model calls over a couple of minutes.  Doing
that inside the POST would mean a reloaded tab throws the work away and a
second click starts a rival run — the same two bugs indexing had before it was
moved off the request.  So the shape is the same one:

    POST starts a run  ->  a worker task drafts  ->  clients open a stream onto
                                                     whatever it is doing

Every event is buffered under an increasing cursor, so a client that comes back
replays what it missed and then follows live.

Unlike indexing, there is no separate run history to write.  The golden set
*is* the durable record: a run that finished left a set behind, and a run
interrupted by a restart left a partial one.  Asking the store what a set holds
answers "what happened" better than a parallel job table would, and cannot
disagree with it.

A pass that fails is reported and the run continues.  Eleven sections drafted
and one failed is a set worth reviewing; aborting the run would throw away
eleven sections' worth of model calls to punish the twelfth.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from pydantic import BaseModel

from app.schemas.golden import (
    GoldenEnqueueResponse,
    GoldenErrorEvent,
    GoldenErrorEventData,
    GoldenRow,
    GoldenRowEvent,
    GoldenRowEventData,
    GoldenRowUpdate,
    GoldenRun,
    GoldenRunRequest,
    GoldenRunState,
    GoldenSetState,
    GoldenStage,
    GoldenStageEvent,
    GoldenStageEventData,
    GoldenStartedEvent,
    GoldenStartedEventData,
    GoldenSummaryEvent,
    GoldenSummaryEventData,
)
from app.schemas.prompt import PromptId
from app.services import (
    golden_catalog,
    golden_facts,
    golden_generator,
    golden_store,
    golden_validator,
    object_store,
    prompt_store,
    text_extraction,
)
from app.services.document_sections import split_sections, titles
from app.services.golden_generator import DraftFailed
from app.services.llm import catalog as model_catalog
from app.services.llm.factory import get_adapter

logger = logging.getLogger(__name__)

# How long a finished run stays readable in memory before the set itself
# becomes the only record of it.
JOB_RETENTION_SECONDS = 3600

# Backstop on the subscriber wait, so a stream re-checks rather than hanging if
# a notification is ever missed.
SUBSCRIBE_POLL_SECONDS = 15.0

# Leading digits and separators stripped from a filename to make a slug, so
# "01-meridian-fy2025-annual-report.txt" offers "meridian-fy2025-annual-report".
_SLUG_PREFIX = "0123456789-_ "

_TERMINAL_STATES = frozenset(
    {GoldenRunState.COMPLETED, GoldenRunState.FAILED, GoldenRunState.ABANDONED}
)


class _Job:
    """One generation run: its event buffer and its running totals."""

    def __init__(self, request: GoldenRunRequest, set_id: str, model: str) -> None:
        self.job_id = uuid.uuid4().hex[:16]
        self.set_id = set_id
        self.source_key = request.source_key
        self.model = model
        self.state = GoldenRunState.RUNNING
        self.stage: Optional[GoldenStage] = None

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
        self.total = 0
        self.row_count = 0

    @property
    def terminal(self) -> bool:
        """Whether the run has stopped, for whatever reason."""
        return self.state in _TERMINAL_STATES

    async def emit(self, event: BaseModel) -> int:
        """Buffer an event and wake every subscriber.

        Args:
            event: The typed event to publish.

        Returns:
            The cursor the event was recorded under.
        """
        async with self.updated:
            self.cursor += 1
            cursor = self.cursor
            self.events.append((cursor, event))
            self.updated.notify_all()
        return cursor


# Every job still held in memory, finished ones included until they age out.
_jobs: dict[str, _Job] = {}

# Guards starting a run, so two clicks cannot open two runs on one source.
_lock = asyncio.Lock()


async def enqueue(request: GoldenRunRequest) -> GoldenEnqueueResponse:
    """Start a generation run and return the id to follow it by.

    Args:
        request: What to draft from, and with which model.

    Returns:
        The run's job id and the set it will fill.

    Raises:
        ValueError: When the provider is unknown or the file cannot be read.
    """
    provider = request.provider or _default_provider()
    model = request.model or model_catalog.DEFAULT_MODELS.get(provider, "")

    # Fail here rather than inside the worker: an unusable provider is the
    # caller's mistake, and a 400 says so where a failed run would not.
    get_adapter(provider)

    text = await _load(request.source_key)
    sections = split_sections(text)
    outline = titles(sections)

    async with _lock:
        _drop_expired()
        golden_set = await golden_store.create(
            source_key=request.source_key,
            slug=request.slug.strip() or slug_for(request.source_key),
            provider=provider,
            model=model,
            sections=outline,
        )
        job = _Job(request, golden_set.set_id, model)
        job.total = len(outline)
        _jobs[job.job_id] = job
        job.task = asyncio.create_task(_worker(job, request, provider, text, sections))

    return GoldenEnqueueResponse(job_id=job.job_id, set_id=golden_set.set_id)


async def _worker(
    job: _Job,
    request: GoldenRunRequest,
    provider: str,
    text: str,
    sections: list,
) -> None:
    """Draft, validate and store a whole set, reporting as it goes."""
    started = time.monotonic()

    try:
        await job.emit(
            GoldenStartedEvent(
                data=GoldenStartedEventData(
                    job_id=job.job_id,
                    set_id=job.set_id,
                    source_key=job.source_key,
                    model=job.model,
                )
            )
        )

        outline = titles(sections)
        await _stage(job, GoldenStage.EXTRACT, f"{len(text):,} characters", 1, 1)
        await _stage(job, GoldenStage.SEGMENT, f"{len(outline)} sections", 1, 1)

        digest = golden_facts.build(sections)
        quota = golden_catalog.plan(sections, len(digest.values), request.density)
        await _stage(
            job, GoldenStage.FACTS, f"{len(digest.values)} distinct figures", 1, 1
        )

        templates = golden_generator.templates_for(await prompt_store.active())
        adapter = get_adapter(provider)

        rows = await _draft_all(job, adapter, templates, sections, digest, outline, quota)

        await _stage(job, GoldenStage.VALIDATE, f"{len(rows)} rows", 0, len(rows))
        checked = golden_validator.validate(rows, digest, outline)
        await _stage(job, GoldenStage.SELF_CHECK, f"{len(checked)} rows", len(checked), len(checked))

        stored = await golden_store.replace_rows(job.set_id, checked)
        for row in stored:
            await job.emit(GoldenRowEvent(data=GoldenRowEventData(row=row)))

        await golden_store.finish(job.set_id, GoldenSetState.READY)
        job.row_count = len(stored)
        job.state = GoldenRunState.COMPLETED
        await _summarise(job, stored, started)

    except asyncio.CancelledError:
        job.state = GoldenRunState.ABANDONED
        await golden_store.finish(job.set_id, GoldenSetState.FAILED, "The run was cancelled.")
        raise
    except Exception as exc:
        logger.exception("Golden set generation failed for %s", job.source_key)
        job.state = GoldenRunState.FAILED
        job.error = str(exc)
        await golden_store.finish(job.set_id, GoldenSetState.FAILED, str(exc))
        await job.emit(
            GoldenErrorEvent(
                data=GoldenErrorEventData(
                    stage=job.stage or GoldenStage.EXTRACT,
                    detail=job.source_key,
                    message=str(exc),
                    fatal=True,
                )
            )
        )
    finally:
        job.finished_at = datetime.now(timezone.utc)
        job.finished_monotonic = time.monotonic()
        async with job.updated:
            job.updated.notify_all()


async def _draft_all(
    job: _Job,
    adapter,
    templates: dict[PromptId, str],
    sections: list,
    digest: golden_facts.FactDigest,
    outline: list[str],
    quota: golden_catalog.GenerationQuota,
) -> list[GoldenRow]:
    """Run all three drafting passes, reporting each and surviving any of them.

    Sections are drafted concurrently but bounded, so a long document takes a
    couple of rounds rather than a dozen sequential calls — without opening a
    connection per section.
    """
    job.stage = GoldenStage.DRAFT
    by_title = {section.title: section for section in sections}
    limit = asyncio.Semaphore(golden_generator.MAX_CONCURRENT_SECTIONS)
    done = 0

    async def one(quota_row) -> list[GoldenRow]:
        section = by_title.get(quota_row.title)
        if section is None:
            return []
        async with limit:
            return await golden_generator.draft_section(
                adapter, job.model, templates[PromptId.GOLDEN_SECTION],
                section, digest, outline, quota_row.count,
            )

    results = await asyncio.gather(
        *(one(q) for q in quota.per_section), return_exceptions=True
    )

    rows: list[GoldenRow] = []
    for quota_row, result in zip(quota.per_section, results):
        done += 1
        if isinstance(result, DraftFailed):
            await _failed(job, GoldenStage.DRAFT, result.detail, result.message)
            continue
        if isinstance(result, BaseException):
            await _failed(job, GoldenStage.DRAFT, quota_row.title, str(result))
            continue
        rows.extend(result)
        await _stage(
            job, GoldenStage.DRAFT, quota_row.title, done, len(quota.per_section)
        )

    rows.extend(
        await _pass(
            job,
            "cross-section",
            golden_generator.draft_cross_section(
                adapter, job.model, templates[PromptId.GOLDEN_CROSS_SECTION],
                digest, outline, quota.cross_section, quota.allow_arithmetic,
            ),
        )
    )
    rows.extend(
        await _pass(
            job,
            "unanswerable",
            golden_generator.draft_unanswerable(
                adapter, job.model, templates[PromptId.GOLDEN_UNANSWERABLE],
                digest, outline, quota.unanswerable,
            ),
        )
    )
    return rows


async def _pass(job: _Job, label: str, coroutine) -> list[GoldenRow]:
    """Run one whole-document pass, reporting a failure rather than raising it."""
    try:
        rows = await coroutine
    except DraftFailed as exc:
        await _failed(job, GoldenStage.DRAFT, exc.detail, exc.message)
        return []
    except Exception as exc:
        await _failed(job, GoldenStage.DRAFT, label, str(exc))
        return []

    await _stage(job, GoldenStage.DRAFT, f"{label}: {len(rows)} rows", 0, 0)
    return rows


async def _stage(
    job: _Job, stage: GoldenStage, detail: str, completed: int, total: int
) -> None:
    """Report that a stage advanced."""
    job.stage = stage
    job.completed = completed
    await job.emit(
        GoldenStageEvent(
            data=GoldenStageEventData(
                stage=stage, detail=detail, completed=completed, total=total
            )
        )
    )


async def _failed(job: _Job, stage: GoldenStage, detail: str, message: str) -> None:
    """Report a pass that failed, without stopping the run."""
    logger.warning("Golden draft pass %r failed: %s", detail, message)
    await job.emit(
        GoldenErrorEvent(
            data=GoldenErrorEventData(
                stage=stage, detail=detail, message=message, fatal=False
            )
        )
    )


async def _summarise(job: _Job, rows: list[GoldenRow], started: float) -> None:
    """Close the stream with the totals for the run."""
    by_type: dict[str, int] = {}
    for row in rows:
        by_type[row.type.value] = by_type.get(row.type.value, 0) + 1

    valid = sum(1 for row in rows if row.status.value == "valid")
    await job.emit(
        GoldenSummaryEvent(
            data=GoldenSummaryEventData(
                set_id=job.set_id,
                slug=(await golden_store.get(job.set_id)).slug,
                row_count=len(rows),
                valid_count=valid,
                flagged_count=len(rows) - valid,
                by_type=by_type,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        )
    )


async def _load(source_key: str) -> str:
    """Fetch a source file and decode it.

    Args:
        source_key: Object key to read.

    Returns:
        The file's plain text.

    Raises:
        FileNotFoundError: When no object exists at that key. Left to propagate
            rather than folded into ValueError: "there is no such file" and
            "that file is unusable" are different answers, and the router owes
            the caller a 404 for one and a 400 for the other.
        ValueError: When the file is empty or of a type we cannot read.
    """
    if not text_extraction.is_supported(source_key):
        raise ValueError(f"Cannot read {source_key!r}: unsupported file type.")

    data = await object_store.get_object(source_key)
    text = await asyncio.to_thread(text_extraction.extract_text, source_key, data)
    if not text.strip():
        raise ValueError(f"{source_key!r} holds no text to draft questions from.")
    return text


async def apply_row_update(
    set_id: str, row_id: str, update: GoldenRowUpdate
) -> GoldenRow:
    """Apply an edit to one row, then re-check it against the source document.

    Re-checking here is the point of the review loop: someone fixes a
    paraphrased answer key and the flag clears in the same response, so they
    can see whether the fix worked rather than guessing. Recording a review
    decision alone changes nothing about whether the row holds up, so that
    path skips the work — and the object-store read that comes with it.

    Args:
        set_id: Set the row belongs to.
        row_id: Row to change.
        update: The fields to change.

    Returns:
        The row as it now stands, with a fresh verdict when its content moved.

    Raises:
        UnknownGoldenSet: When the set does not exist.
        UnknownGoldenRow: When the row is not in that set.
    """
    row = await golden_store.update_row(set_id, row_id, update)
    touched = update.model_dump(exclude_unset=True, exclude_none=True)

    # Dropping a row shortens the set, so every id after it moves. Renumbering
    # here rather than at export keeps the id a person sees in the review table
    # and the id the exported file carries as one number — an export whose ids
    # disagree with the screen is worse than one with a gap.
    if "review" in touched:
        for renumbered in await golden_store.renumber(set_id):
            if renumbered.row_id == row_id:
                row = renumbered
                break

    if not any(field != "review" for field in touched):
        return row

    golden_set = await golden_store.get(set_id)
    digest, outline = await facts_for(golden_set.source_key)
    checked = golden_validator.validate([row], digest, outline)[0]
    await golden_store.record_check(set_id, row_id, checked.status, checked.issues)
    return checked


async def facts_for(source_key: str) -> tuple[golden_facts.FactDigest, list[str]]:
    """Load a source file and index what it states.

    The same three steps a run opens with, factored out because re-checking an
    edited row needs exactly them and nothing else.

    Args:
        source_key: Object key to read.

    Returns:
        The document's facts, and the titles a row may cite.

    Raises:
        ValueError: When the file is missing, empty, or unreadable.
    """
    sections = split_sections(await _load(source_key))
    return golden_facts.build(sections), titles(sections)


def slug_for(source_key: str) -> str:
    """Derive a default filename stem from an object key.

    Corpus files are numbered for ordering, and that ordering is not part of
    what the set is about — so "01-meridian-fy2025-annual-report.txt" offers
    "meridian-fy2025-annual-report", which a person can shorten before export.

    Args:
        source_key: The object key.

    Returns:
        A filename stem, never empty.
    """
    stem = source_key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.lstrip(_SLUG_PREFIX) or stem or "golden-set"


def _default_provider() -> str:
    """The first provider this deployment can actually use.

    Asked of the catalog rather than the config, so a run started without a
    provider fails on a missing key at most once — the catalog already knows
    which credentials are present.

    Returns:
        A provider key. The first configured one when nothing is available, so
        the resulting error names a real provider rather than an empty string.
    """
    entries = model_catalog.list_models()
    for entry in entries:
        if entry["available"]:
            return entry["provider"]
    return entries[0]["provider"] if entries else ""


def _drop_expired() -> None:
    """Forget finished runs once the set itself is the better record."""
    now = time.monotonic()
    for job_id, job in list(_jobs.items()):
        if job.finished_monotonic and now - job.finished_monotonic > JOB_RETENTION_SECONDS:
            _jobs.pop(job_id, None)


def get(job_id: str) -> Optional[GoldenRun]:
    """One run, as a client reopening the stream finds it."""
    job = _jobs.get(job_id)
    return _to_run(job) if job else None


def _to_run(job: _Job) -> GoldenRun:
    """Describe a job across the API boundary."""
    return GoldenRun(
        job_id=job.job_id,
        set_id=job.set_id,
        source_key=job.source_key,
        state=job.state,
        stage=job.stage,
        completed=job.completed,
        total=job.total,
        row_count=job.row_count,
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
            stream follows live. The default replays from the beginning, which
            is what a client that just reloaded wants.

    Yields:
        Each event with its cursor.
    """
    job = _jobs.get(job_id)
    if job is None:
        # Aged out, or from before a restart. The set it left behind is the
        # record now, and the client reads that instead.
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
