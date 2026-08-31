"""Times each stage of a request's pipeline and reports them as they happen.

A streaming answer hides its own latency: the client sees a spinner, then text,
with no account of where the seconds went. This records each stage — what ran,
how long it took, and what it produced — so that account can be streamed
alongside the answer.

The point is that stages are declared where the work is, not listed anywhere
central. A service wraps a step in `timeline.stage(...)` and the step appears on
the client by itself; nothing in the router, the schema, or the UI enumerates
the stages, so adding one is a single change at the place it runs.

Framework-agnostic on purpose: this knows nothing about SSE or HTTP. It records
events, and the caller decides how to put them on a wire.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Optional, TypeVar

from app.schemas.chat import StageEventData

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Stage:
    """One timed step of a pipeline, from the moment it starts.

    Handed to the code doing the work so it can attach a detail — the one line
    a person reads to know what this stage actually did, rather than only how
    long it took.
    """

    def __init__(self, sequence: int, name: str, label: str) -> None:
        """Begin timing a stage.

        Args:
            sequence: Position in the timeline; identifies the stage across its
                start and end events, so a client updates one row rather than
                appending a second.
            name: Stable machine-readable id (e.g. "embedding").
            label: Human wording shown to a person (e.g. "Embedding the question").
        """
        self.sequence = sequence
        self.name = name
        self.label = label
        self.detail = ""
        self.error = ""
        self._started = time.perf_counter()

    def note(self, detail: str) -> None:
        """Describe what this stage produced, in wording a person can read."""
        self.detail = detail

    def fail(self, message: str) -> None:
        """Mark the stage as failed without raising.

        For a stage whose failure the pipeline survives: the caller handles the
        exception itself, but the timeline must still show the step as failed
        rather than quietly completed.
        """
        self.error = message

    @property
    def elapsed_ms(self) -> int:
        """Milliseconds since the stage started."""
        return max(0, round((time.perf_counter() - self._started) * 1000))

    def started_event(self) -> StageEventData:
        """The event announcing this stage is running."""
        return StageEventData(
            sequence=self.sequence,
            name=self.name,
            label=self.label,
            status="started",
            elapsed_ms=0,
        )

    def ended_event(self) -> StageEventData:
        """The event closing this stage, carrying its duration and outcome."""
        return StageEventData(
            sequence=self.sequence,
            name=self.name,
            label=self.label,
            status="failed" if self.error else "completed",
            elapsed_ms=self.elapsed_ms,
            detail=self.error or self.detail,
        )


class Timeline:
    """Collects stage events for one request, in the order they occurred.

    Events are buffered rather than pushed, because the caller owns the wire:
    the chat endpoint interleaves them with answer text, and only it knows when
    a frame may be written.
    """

    def __init__(self) -> None:
        self._events: list[StageEventData] = []
        self._sequence = 0
        # Lets a consumer wait for the next event instead of polling for it.
        self._arrived = asyncio.Event()

    @asynccontextmanager
    async def stage(self, name: str, label: str) -> AsyncIterator[Stage]:
        """Time the block it wraps and record its start and end.

        An exception is recorded as a failure and re-raised — a stage that blew
        up must not be left looking like one that finished.

        Args:
            name: Stable machine-readable id for the stage.
            label: Human wording for the stage.

        Yields:
            The stage, so the work can attach a detail to it.
        """
        self._sequence += 1
        stage = Stage(self._sequence, name, label)
        self._record(stage.started_event())
        try:
            yield stage
        except Exception as exc:
            stage.fail(str(exc))
            raise
        finally:
            self._record(stage.ended_event())

    def drain(self) -> list[StageEventData]:
        """Take every event recorded since the last drain."""
        events, self._events = self._events, []
        return events

    async def follow(self, work: Awaitable[T]) -> AsyncIterator[StageEventData]:
        """Run an awaitable, yielding its stage events while it is still running.

        Without this a slow service reports all of its stages at once, after the
        fact — which is precisely when the timing no longer tells anyone
        anything. The awaitable's own result is left on the task for the caller
        to read, because an async generator cannot return one.

        Args:
            work: The pipeline call to run, which records stages as it goes.

        Yields:
            Each stage event as it is recorded, then whatever the work left
            behind when it ended.
        """
        task = asyncio.ensure_future(work)
        # A finished task must wake the consumer too, or the last events sit in
        # the buffer until something else happens to be recorded.
        task.add_done_callback(lambda _: self._arrived.set())

        try:
            while True:
                for event in self.drain():
                    yield event

                if task.done():
                    return

                self._arrived.clear()
                # Re-check after clearing: an event recorded between the drain
                # and the clear would otherwise wait for the next one to arrive.
                if self._events or task.done():
                    continue

                await self._arrived.wait()
        finally:
            # The consumer stopped early — the client hung up, most likely.
            # Nothing will read the result, so don't leave the work running.
            if not task.done():
                task.cancel()

    def _record(self, event: StageEventData) -> None:
        """Buffer an event and log it, then wake anyone waiting on one."""
        self._events.append(event)
        self._arrived.set()

        if event.status == "started":
            logger.info("stage %s started — %s", event.name, event.label)
        else:
            logger.info(
                "stage %s %s in %dms%s",
                event.name,
                event.status,
                event.elapsed_ms,
                f" — {event.detail}" if event.detail else "",
            )


def detached() -> Timeline:
    """A timeline nobody is reading, for callers that only want the work done.

    Services take a timeline so their stages can be reported; a caller outside a
    request (an evaluation run, a script) has nowhere to report them, and should
    not have to care.
    """
    return Timeline()


__all__ = ["Stage", "Timeline", "detached"]
