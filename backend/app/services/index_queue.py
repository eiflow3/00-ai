"""Index queue — the worker that embeds files, and the stream that reports it.

Indexing used to *be* the HTTP response: the pipeline generator was handed
straight to an SSE response, so a client that reloaded cancelled the work
mid-file and lost embeddings it had already paid for. Worse, a second Index
click aborted the first stream, and with it the first run.

So the work is moved off the request entirely:

    POST enqueues  ->  one worker drains the queue  ->  clients open a stream
                                                        onto whatever it is doing

Three consequences worth stating, because each one removes a class of bug:

  * **A response ending touches nothing.** The worker is a task on the event
    loop, not part of any request's task group, so reloading, closing the tab or
    losing the network leaves the run alone.
  * **A second click joins the work.** One queue, one worker — so clicking Index
    on three rows enqueues three files rather than starting three rival runs.
    That is also why there is only ever one stream to attach to, and no need to
    multiplex or to worry about the browser's per-host connection limit.
  * **Progress survives a reload.** Every event is buffered under an increasing
    cursor, so a client that comes back replays what it missed and then follows
    live. The same buffer is written to run history, which is what lets a
    finished run be read back after the process that ran it is gone.

The queue's *state* — what waits, what is in flight — lives in
app.services.index_registry, so a status row can read it without importing this
module and its dependency on the whole pipeline.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from pydantic import BaseModel

from app.config import settings
from app.schemas.ingestion import (
    EnqueueResponse,
    IndexCompletedEvent,
    IndexCompletedEventData,
    IndexErrorEvent,
    IndexErrorEventData,
    IndexProgressEvent,
    IndexProgressEventData,
    IndexQueuedEvent,
    IndexQueuedEventData,
    IndexRequest,
    IndexRun,
    IndexRunState,
    IndexStartedEvent,
    IndexStartedEventData,
    IndexSummaryEvent,
    IndexSummaryEventData,
)
from app.schemas.source import IndexState, SourceStatus
from app.services import (
    chunk_variants,
    index_registry,
    ingestion,
    run_store,
    source_cache,
    sync_status,
)
from app.services.index_registry import QueuedFile
from app.services.text_extraction import UnsupportedSourceType

logger = logging.getLogger(__name__)

# How long a finished run's live buffer is kept before it is dropped. After
# that a client re-attaching is served from run history instead, which holds the
# same events — this only bounds memory.
JOB_RETENTION_SECONDS = 15 * 60

# How long a subscriber waits for a new event before looking again. A backstop
# against a missed notification leaving a stream hung; the condition variable is
# what normally wakes it.
SUBSCRIBE_POLL_SECONDS = 15.0

_TERMINAL_STATES = frozenset(
    {
        IndexRunState.COMPLETED,
        IndexRunState.FAILED,
        IndexRunState.CANCELLED,
        IndexRunState.ABANDONED,
    }
)


class _Job:
    """One run: its geometry, its event buffer, and its running totals.

    Internal to this module. What crosses the API is `IndexRun`, built from it.
    """

    def __init__(self, request: IndexRequest) -> None:
        self.job_id = uuid.uuid4().hex[:16]
        self.state = IndexRunState.RUNNING

        # The embedding model is fixed for the run: mixing two of them in one
        # index would make similarity scores meaningless, and that is a
        # different feature from mixing chunk shapes.
        self.embedding_model = request.embedding_model

        # Chunk geometry is *not* fixed here. It rides on each queued entry
        # instead, because embedding one document under four strategies is the
        # ordinary case now, and a run-wide geometry would silently embed three
        # of the four the first one's way.
        self.chunk_size = request.chunk_size
        self.chunk_overlap = request.chunk_overlap

        # Events emitted so far, each under an increasing cursor. This is what
        # a re-attaching client replays.
        self.events: list[tuple[int, BaseModel]] = []
        self.cursor = -1

        # Wakes every subscriber when an event lands. A condition rather than an
        # event, so one subscriber consuming a notification cannot starve
        # another — two tabs watching the same run is an ordinary case.
        self.updated = asyncio.Condition()

        self.task: Optional[asyncio.Task] = None
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: Optional[datetime] = None
        self.finished_monotonic: Optional[float] = None
        self.error = ""

        # Totals. `total` counts every file the run has taken on, and grows when
        # the queue does.
        self.total = 0
        self.indexed = 0
        self.skipped = 0
        self.failed = 0
        self.total_chunks = 0
        self.total_reused = 0
        self.total_pruned = 0

        # Keys whose final status the closing summary should report.
        self.processed_keys: list[str] = []

    @property
    def terminal(self) -> bool:
        """Whether the run has stopped, for whatever reason."""
        return self.state in _TERMINAL_STATES

    async def emit(self, event: BaseModel) -> int:
        """Buffer an event, persist it, and wake every subscriber.

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

        # Written outside the condition so a slow disk cannot hold up the run.
        name = str(getattr(event, "event", "message"))
        payload = getattr(event, "data", None)
        await run_store.event_recorded(
            self.job_id,
            cursor,
            name,
            payload.model_dump_json() if payload is not None else "{}",
        )
        return cursor


# The run in flight, if any. At most one, by design.
_active: Optional[_Job] = None

# Every job still held in memory, finished ones included until they age out.
_jobs: dict[str, _Job] = {}

# Guards the decision to start, join or finish a run. Taken before the
# registry's own lock, always in that order, so the two cannot deadlock.
_lock = asyncio.Lock()


# --- Enqueuing ------------------------------------------------------------


async def enqueue(request: IndexRequest) -> EnqueueResponse:
    """Accept files for indexing, starting a run if none is going.

    Args:
        request: Which files to index, and how to chunk and embed them.

    Returns:
        The run the files joined, what was accepted, and what was not.
    """
    keys, missing = await _resolve_keys(request)

    # A named variant decides both how the files are cut and where they land,
    # and is validated here so a bad name is a rejected request rather than a
    # namespace quietly brought into existence by the first write.
    config, variant = chunk_variants.resolve(request.variant, request.chunking)

    async with _lock:
        job = _active
        started = False

        if job is None or job.terminal:
            job = _Job(request)
            _jobs[job.job_id] = job
            started = True

        accepted, already, rejected = await index_registry.enqueue(
            keys,
            settings.max_index_queue,
            variant=variant,
            config=config,
            force=request.force,
        )
        job.total += len(accepted)

        if started:
            globals()["_active"] = job
            await run_store.run_started(
                job.job_id,
                accepted,
                job.embedding_model,
                job.chunk_size,
                job.chunk_overlap,
            )
            await job.emit(
                IndexStartedEvent(
                    data=IndexStartedEventData(
                        job_id=job.job_id,
                        keys=accepted,
                        total=job.total,
                        embedding_model=job.embedding_model,
                    )
                )
            )
            job.task = asyncio.create_task(
                _worker(job), name=f"index-worker-{job.job_id}"
            )
            logger.info(
                "Run %s started with %d file(s): %s",
                job.job_id,
                len(accepted),
                ", ".join(accepted) or "none",
            )
        elif accepted:
            # Joining a run already in flight; its totals have to move.
            await run_store.run_queue_changed(job.job_id, index_registry.pending())
            await job.emit(
                IndexQueuedEvent(
                    data=IndexQueuedEventData(
                        added=accepted,
                        pending=index_registry.pending(),
                        total=job.total,
                    )
                )
            )
            logger.info(
                "Run %s took on %d more file(s): %s",
                job.job_id,
                len(accepted),
                ", ".join(accepted),
            )

        # Keys named but absent from storage are reported here rather than as
        # run events: nothing will happen to them, so they are the caller's
        # problem to see immediately.
        for key in missing:
            await job.emit(
                IndexErrorEvent(
                    data=IndexErrorEventData(
                        source_key=key,
                        stage="loading",
                        message="No object at this key in storage.",
                    )
                )
            )

        if rejected:
            logger.warning(
                "Run %s refused %d file(s): the queue limit of %d is reached.",
                job.job_id,
                len(rejected),
                settings.max_index_queue,
            )

        return EnqueueResponse(
            job_id=job.job_id,
            accepted=accepted,
            already_queued=already,
            rejected=rejected,
            missing=missing,
            limit=settings.max_index_queue,
            pending=index_registry.pending(),
        )


async def _resolve_keys(request: IndexRequest) -> tuple[list[str], list[str]]:
    """Work out which files a request refers to.

    Args:
        request: The enqueue request.

    Returns:
        The keys to queue, and the named keys that storage does not have.
    """
    if request.keys:
        # Explicit keys are honoured as given; the caller has already chosen.
        found = await asyncio.gather(
            *(ingestion.resolve_source(key) for key in request.keys)
        )
        keys = [key for key, source in zip(request.keys, found) if source is not None]
        missing = [key for key, source in zip(request.keys, found) if source is None]
        return keys, missing

    # Otherwise let the storage-versus-index comparison pick the work — against
    # the space this run writes to, so a sweep aimed at one variant is not told
    # a file is current on the strength of a different variant's copy.
    statuses = await sync_status.list_reindexable(
        prefix=request.prefix,
        only_stale=request.only_stale and not request.force,
        variant=request.variant,
    )
    return [status.source_key for status in statuses if status.source], []


# --- The worker -----------------------------------------------------------


async def _worker(job: _Job) -> None:
    """Drain the queue one file at a time until it is empty.

    Runs as an independent task, which is what lets every client disconnect
    without touching the work.
    """
    try:
        while True:
            # Deciding to stop and accepting new work must not interleave, or a
            # file enqueued at the moment the queue empties would be stranded
            # with no worker to pick it up.
            async with _lock:
                entry = await index_registry.dequeue()
                if entry is None:
                    job.state = IndexRunState.COMPLETED
                    break

            try:
                await _process(job, entry)
            finally:
                index_registry.release()

    except asyncio.CancelledError:
        job.state = IndexRunState.CANCELLED
        job.error = "Stopped before the queue was empty."
        logger.info("Run %s cancelled", job.job_id)
        # Awaiting here is safe and necessary. Safe because the cancellation has
        # already been delivered and caught, so a further await is not
        # interrupted again; necessary because a subscriber is waiting for the
        # closing summary, and deferring the close to a task of its own would
        # race the stream's own decision that the run has ended.
        await _close(job)
        raise

    except Exception as exc:
        job.state = IndexRunState.FAILED
        job.error = str(exc)
        logger.exception("Run %s failed: %s", job.job_id, exc)
        await _close(job)

    else:
        logger.info(
            "Run %s finished: %d indexed, %d skipped, %d failed, %d chunk(s) reused",
            job.job_id,
            job.indexed,
            job.skipped,
            job.failed,
            job.total_reused,
        )
        await _close(job)

    finally:
        index_registry.release()


async def _process(job: _Job, entry: QueuedFile) -> None:
    """Index one file on the terms it was queued with.

    The entry carries its own chunking configuration and its own destination,
    so two entries for the same file cut it two different ways and write to two
    different places — which is the whole of how a chunking comparison is run.
    """
    source_key = entry.source_key
    space = chunk_variants.space_for(entry.variant)

    await run_store.file_started(job.job_id, source_key, entry.variant)

    source = await ingestion.resolve_source(source_key)
    if source is None:
        # Queued earlier, deleted since. Not a failure of the pipeline.
        job.failed += 1
        await run_store.file_finished(
            job.job_id,
            source_key,
            "missing",
            error="Deleted before it was indexed.",
            variant=entry.variant,
        )
        await job.emit(
            IndexErrorEvent(
                data=IndexErrorEventData(
                    source_key=source_key,
                    stage="loading",
                    message="The file was deleted before the run reached it.",
                )
            )
        )
        return

    position = len(job.processed_keys) + 1
    result = ingestion.IngestionResult(source_key)

    try:
        async for stage, result in ingestion.index_source(
            source,
            entry.config,
            job.embedding_model,
            force=entry.force,
            space=space,
        ):
            await job.emit(
                IndexProgressEvent(
                    data=IndexProgressEventData(
                        source_key=source_key,
                        stage=stage,
                        file_number=position,
                        total_files=job.total,
                        chunk_count=result.chunk_count,
                    )
                )
            )

    except UnsupportedSourceType as exc:
        # Not a failure of the run — just a file this pipeline cannot read.
        job.skipped += 1
        job.processed_keys.append(source_key)
        await run_store.file_finished(
            job.job_id,
            source_key,
            IndexState.UNSUPPORTED.value,
            error=str(exc),
            variant=entry.variant,
        )
        await job.emit(
            IndexCompletedEvent(
                data=IndexCompletedEventData(
                    source_key=source_key, skipped=True, state=IndexState.UNSUPPORTED
                )
            )
        )
        await job.emit(
            IndexErrorEvent(
                data=IndexErrorEventData(
                    source_key=source_key, stage="extraction", message=str(exc)
                )
            )
        )
        return

    except asyncio.CancelledError:
        # Stop was pressed mid-file. Record what we know before unwinding, so
        # the history does not simply lose the file. Whatever vectors did land
        # are real, so the cached view of this file is out of date either way.
        await source_cache.invalidate(source_key)
        await run_store.file_finished(
            job.job_id,
            source_key,
            IndexRunState.CANCELLED.value,
            chunk_count=result.chunk_count,
            reused=result.reused,
            error="Stopped mid-file.",
            variant=entry.variant,
        )
        raise

    except Exception as exc:
        # One file failing must not end the run; report it and carry on.
        job.failed += 1
        logger.exception("Run %s: %s failed: %s", job.job_id, source_key, exc)
        await run_store.file_finished(
            job.job_id, source_key, "failed", error=str(exc), variant=entry.variant
        )
        await job.emit(
            IndexErrorEvent(
                data=IndexErrorEventData(
                    source_key=source_key, stage="indexing", message=str(exc)
                )
            )
        )
        return

    # Non-fatal trouble along the way — a table left undescribed, say. The
    # file still indexed; each warning becomes an error event so a client
    # watching the stream sees exactly what was degraded, and the run goes on.
    for warning in result.warnings:
        await job.emit(
            IndexErrorEvent(
                data=IndexErrorEventData(
                    source_key=source_key, stage="describing_tables", message=warning
                )
            )
        )

    job.indexed += 1
    job.total_chunks += result.chunk_count
    job.total_reused += result.reused
    job.total_pruned += result.pruned
    job.processed_keys.append(source_key)

    # Per file rather than per run: a client watching the stream refreshes its
    # list as each file completes, and would otherwise be served the listing as
    # it stood before the run started. A variant writes nowhere the sources
    # listing reads, so there is nothing of its to invalidate.
    if not entry.variant:
        await source_cache.invalidate(source_key)

    await run_store.file_finished(
        job.job_id,
        source_key,
        IndexState.CURRENT.value,
        chunk_count=result.chunk_count,
        reused=result.reused,
        pruned=result.pruned,
        variant=entry.variant,
    )
    await job.emit(
        IndexCompletedEvent(
            data=IndexCompletedEventData(
                source_key=source_key,
                chunk_count=result.chunk_count,
                reused=result.reused,
                pruned=result.pruned,
                skipped=False,
                state=IndexState.CURRENT,
            )
        )
    )


async def _close(job: _Job) -> None:
    """Emit the closing summary and write the run's totals to history."""
    job.finished_at = datetime.now(timezone.utc)
    job.finished_monotonic = time.monotonic()

    # Re-read each file's status from both sides rather than asserting it, so
    # the client's list refreshes from what is actually stored. Deduplicated
    # because one run can process the same file several times — once per
    # chunking variant — and its status is the same after each.
    statuses: list[SourceStatus] = []
    keys = list(dict.fromkeys(job.processed_keys))
    if keys:
        statuses = list(
            await asyncio.gather(*(sync_status.get_status(key) for key in keys))
        )

    await job.emit(
        IndexSummaryEvent(
            data=IndexSummaryEventData(
                indexed=job.indexed,
                skipped=job.skipped,
                failed=job.failed,
                total_chunks=job.total_chunks,
                total_reused=job.total_reused,
                total_pruned=job.total_pruned,
                statuses=statuses,
            )
        )
    )

    await run_store.run_finished(
        job.job_id,
        job.state.value,
        indexed=job.indexed,
        skipped=job.skipped,
        failed=job.failed,
        total_chunks=job.total_chunks,
        total_reused=job.total_reused,
        total_pruned=job.total_pruned,
        error=job.error,
    )

    # Wake anyone still waiting, so a subscriber sees the terminal state rather
    # than sitting on the condition until its poll interval expires.
    async with job.updated:
        job.updated.notify_all()

    if _active is job:
        globals()["_active"] = None

    _drop_expired()


def _drop_expired() -> None:
    """Forget finished runs whose live buffer has aged out.

    Their events are in run history, so a late re-attach is still served — this
    only stops the process holding every run it has ever done.
    """
    now = time.monotonic()
    for job_id, job in list(_jobs.items()):
        if (
            job.terminal
            and job.finished_monotonic is not None
            and now - job.finished_monotonic > JOB_RETENTION_SECONDS
        ):
            del _jobs[job_id]


# --- Reading and attaching ------------------------------------------------


def _to_run(job: _Job) -> IndexRun:
    """Describe a live job as the record clients read."""
    in_flight = index_registry.in_flight()
    return IndexRun(
        job_id=job.job_id,
        state=job.state,
        pending=index_registry.pending() if not job.terminal else [],
        current=next(iter(in_flight), "") if not job.terminal else "",
        total=job.total,
        indexed=job.indexed,
        skipped=job.skipped,
        failed=job.failed,
        total_reused=job.total_reused,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        last_cursor=job.cursor,
    )


def current() -> Optional[IndexRun]:
    """The run in flight, or None when nothing is being indexed."""
    if _active is None or _active.terminal:
        return None
    return _to_run(_active)


def get(job_id: str) -> Optional[IndexRun]:
    """One run still held in memory, live or recently finished."""
    job = _jobs.get(job_id)
    return _to_run(job) if job else None


async def recent(limit: int = 10) -> list[IndexRun]:
    """Runs to show a client: the live one first, then history.

    History comes from SQLite, so this still answers after a restart — which is
    exactly when a person wants to know what the last run did.
    """
    runs: list[IndexRun] = []

    live = current()
    if live is not None:
        runs.append(live)

    for row in await run_store.recent_runs(limit):
        if live is not None and row["job_id"] == live.job_id:
            continue
        runs.append(_from_history(row))

    return runs[:limit] if limit else runs


def _from_history(row: dict) -> IndexRun:
    """Rebuild a run record from its history row."""
    return IndexRun(
        job_id=row["job_id"],
        state=IndexRunState(row["state"]),
        pending=[],
        current="",
        total=len(row.get("queued_keys") or []),
        indexed=row.get("indexed") or 0,
        skipped=row.get("skipped") or 0,
        failed=row.get("failed") or 0,
        total_reused=row.get("total_reused") or 0,
        started_at=_to_datetime(row.get("started_at")),
        finished_at=_to_datetime(row.get("finished_at")),
        error=row.get("error") or "",
        last_cursor=-1,
    )


def _to_datetime(value: Optional[float]) -> Optional[datetime]:
    """Read a stored epoch timestamp back into an aware datetime."""
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


async def subscribe(job_id: str, after: int = -1) -> AsyncIterator[tuple[int, BaseModel]]:
    """Follow one run's events, replaying what came before `after`.

    Args:
        job_id: The run to follow.
        after: Cursor already seen. Everything past it is replayed, then the
            stream follows live. The default replays from the beginning, which
            is what a client that just reloaded wants.

    Yields:
        Each event with its cursor.
    """
    job = _jobs.get(job_id)

    # Finished and aged out of memory, or from before a restart: history holds
    # the same events, so the client is served the trace rather than an error.
    if job is None:
        for row in await run_store.run_events(job_id, after):
            yield int(row["cursor"]), _RawEvent(row["event"], row["payload"])
        return

    while True:
        # Snapshot under the condition so an event landing mid-iteration cannot
        # be missed or yielded twice.
        async with job.updated:
            pending = [item for item in job.events if item[0] > after]

        for cursor, event in pending:
            yield cursor, event
            after = cursor

        if job.terminal:
            # Anything emitted between the snapshot and here — the summary, in
            # practice — still has to go out before the stream closes.
            async with job.updated:
                remaining = [item for item in job.events if item[0] > after]
            for cursor, event in remaining:
                yield cursor, event
            return

        async with job.updated:
            try:
                await asyncio.wait_for(
                    job.updated.wait(), timeout=SUBSCRIBE_POLL_SECONDS
                )
            except asyncio.TimeoutError:
                # Backstop only; loop round and re-check rather than hanging.
                pass


class _RawEvent:
    """An event replayed from history, already serialised.

    History stores the payload as JSON, so there is nothing to re-validate —
    and re-parsing it into its model only to dump it again would be work for
    no gain. This carries the two fields the router needs.
    """

    def __init__(self, event: str, payload: str) -> None:
        self.event = event
        self.payload = payload


async def cancel(job_id: str) -> bool:
    """Stop a run and clear whatever was still waiting.

    Args:
        job_id: The run to stop.

    Returns:
        True if a running job was stopped, False if there was nothing to stop.
    """
    async with _lock:
        job = _jobs.get(job_id)
        if job is None or job.terminal:
            return False

        dropped = index_registry.clear()
        logger.info(
            "Run %s stopping; %d queued file(s) dropped", job_id, len(dropped)
        )

        task = job.task
        if task is not None:
            task.cancel()

    # Waited on outside the lock, which the worker may still need on its way
    # out. Without this the caller would read the run as still running, because
    # the cancellation has been requested but not yet delivered.
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return True
