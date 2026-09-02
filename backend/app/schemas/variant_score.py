"""Scoring a chunking variant — what a comparison run asks for and reports.

Reading two answers side by side tells you which one you preferred that time.
It does not tell you which way of cutting the document retrieves better, and on
four strategies that are all roughly reasonable it never will.  So the same
questions are put to every variant and the results are counted.

The headline is **retrieval recall**, not correctness.  Whether the passage the
answer needed came back is chunking's job; whether the answer reads well is the
model's.  Judging on the answer alone confounds the two, and a good model will
paper over a mediocre retrieval often enough to hide a real difference.
"""

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.chunking import ChunkingConfig


class VariantScoreRequest(BaseModel):
    """Ask for one golden set to be run against several chunking variants."""

    set_id: str = Field(..., min_length=1, description="Golden set to score against")

    # Which variants to compare. Empty means every variant that holds the set's
    # source file, which is what "score everything I have indexed" looks like.
    variants: list[str] = Field(
        default_factory=list,
        description="Variant ids to score. Empty means every variant holding the file.",
    )

    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Chunks each question retrieves, held equal across variants",
    )

    # Whether to generate an answer, or measure only what came back.
    #
    # Generation is one model call per question per variant, which is the
    # expensive part by a wide margin. Retrieval alone still answers the
    # question this feature exists for, so it is a choice rather than a
    # requirement.
    generate: bool = Field(
        default=True,
        description="Answer each question with the model, not just retrieve for it",
    )

    provider: str = Field(default="", description="LLM provider, when generating")

    model: str = Field(default="", description="Model id, when generating")


class RowScore(BaseModel):
    """How one variant did on one question."""

    question_id: str = Field(..., description="The golden row's exported id")

    question: str = Field(default="", description="The question as asked")

    # Whether the generated answer passed the row's checks. None when the run
    # did not generate — absent, rather than a false that would read as failed.
    correct: Optional[bool] = Field(
        default=None, description="Whether the answer passed, if one was generated"
    )

    recall: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Share of the row's gold sections that were retrieved",
    )

    precision: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Share of retrieved sections that were gold",
    )

    # The best similarity score among the chunks retrieved. Diagnostic: a row
    # that missed with a top score of 0.34 is a different failure from one that
    # missed at 0.81.
    top_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Best similarity score retrieved"
    )

    gold_sections: list[str] = Field(
        default_factory=list, description="Sections the answer should have come from"
    )

    retrieved_sections: list[str] = Field(
        default_factory=list, description="Sections the chunks actually came from"
    )

    answer: str = Field(default="", description="What the model answered, if it did")

    reasons: list[str] = Field(
        default_factory=list, description="Why the answer was marked wrong"
    )

    error: str = Field(default="", description="What failed, if this row failed")


class VariantScore(BaseModel):
    """One variant's result across the whole golden set."""

    variant_id: str = Field(..., description="The variant scored")

    label: str = Field(default="", description="Human-readable variant name")

    config: Optional[ChunkingConfig] = Field(
        default=None, description="How this variant was cut"
    )

    rows: int = Field(default=0, ge=0, description="Questions put to it")

    correct: int = Field(default=0, ge=0, description="Answers that passed")

    # Averaged over the rows that had gold sections to compare against. A row
    # with none — an unanswerable question — is left out rather than counted as
    # a miss, which would punish a variant for a row nothing could retrieve.
    recall: float = Field(default=0.0, ge=0.0, le=1.0, description="Mean recall")

    precision: float = Field(default=0.0, ge=0.0, le=1.0, description="Mean precision")

    failed: int = Field(default=0, ge=0, description="Rows that errored")

    duration_seconds: float = Field(default=0.0, ge=0.0, description="How long it took")

    scores: list[RowScore] = Field(
        default_factory=list, description="Per-question results, in set order"
    )


class ScoreStartedEventData(BaseModel):
    """Payload of the `started` event, sent once before any question is asked."""

    job_id: str = Field(default="", description="Id of the run this stream reports on")
    set_id: str = Field(default="", description="Golden set being used")
    source_key: str = Field(default="", description="File the questions are about")
    variants: list[str] = Field(default_factory=list, description="Variants being scored")
    rows: int = Field(default=0, ge=0, description="Questions per variant")
    generating: bool = Field(default=False, description="Whether answers are generated")


class ScoreStartedEvent(BaseModel):
    """Opens the stream with the run's scope."""

    event: Literal["started"] = Field(default="started", description="The SSE event name")
    data: ScoreStartedEventData = Field(..., description="What this run will do")


class ScoreProgressEventData(BaseModel):
    """Payload of the `progress` event, sent as each question is answered."""

    variant_id: str = Field(..., description="Variant this question was put to")
    completed: int = Field(default=0, ge=0, description="Questions done, across the run")
    total: int = Field(default=0, ge=0, description="Questions the run will ask")
    score: RowScore = Field(..., description="How that question went")


class ScoreProgressEvent(BaseModel):
    """Reports one question, answered by one variant."""

    event: Literal["progress"] = Field(default="progress", description="The SSE event name")
    data: ScoreProgressEventData = Field(..., description="The question just scored")


class ScoreVariantEvent(BaseModel):
    """Reports one variant finishing the whole set."""

    event: Literal["variant"] = Field(default="variant", description="The SSE event name")
    data: VariantScore = Field(..., description="That variant's totals and rows")


class ScoreErrorEventData(BaseModel):
    """Payload of the `error` event. The run continues with what is left."""

    variant_id: str = Field(default="", description="Variant that failed")
    question_id: str = Field(default="", description="Question that failed, if one did")
    message: str = Field(..., description="What went wrong")


class ScoreErrorEvent(BaseModel):
    """Reports a failure that did not end the run."""

    event: Literal["error"] = Field(default="error", description="The SSE event name")
    data: ScoreErrorEventData = Field(..., description="What failed, and where")


class ScoreSummaryEventData(BaseModel):
    """Payload of the `summary` event, sent once at the very end."""

    # Best first, by retrieval recall — the measure chunking is actually
    # responsible for. Ties break on correctness.
    scores: list[VariantScore] = Field(
        default_factory=list, description="Every variant's result, best first"
    )

    winner: str = Field(default="", description="Variant with the best retrieval recall")

    duration_seconds: float = Field(default=0.0, ge=0.0, description="Total time")


class ScoreSummaryEvent(BaseModel):
    """Closes the stream with the ranking."""

    event: Literal["summary"] = Field(default="summary", description="The SSE event name")
    data: ScoreSummaryEventData = Field(..., description="The comparison, ranked")


class ScoreRunState(str, Enum):
    """Where a scoring run stands. Every state but `running` is terminal."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScoreRun(BaseModel):
    """One scoring run, live or just finished.

    Held in memory only. Unlike an indexing run there is nothing durable to
    write: a score is a measurement of an index at a moment, and re-running it
    after the index changed is the honest way to get it again.
    """

    job_id: str = Field(..., description="Id of this run")
    set_id: str = Field(default="", description="Golden set being used")
    state: ScoreRunState = Field(..., description="Where this run stands")
    variants: list[str] = Field(default_factory=list, description="Variants being scored")
    completed: int = Field(default=0, ge=0, description="Questions answered so far")
    total: int = Field(default=0, ge=0, description="Questions the run will ask")
    started_at: Optional[datetime] = Field(default=None, description="When it began")
    finished_at: Optional[datetime] = Field(default=None, description="When it ended")
    error: str = Field(default="", description="Failure detail, if it failed")
    last_cursor: int = Field(
        default=-1, description="Cursor of the last event emitted by this run"
    )


class ScoreEnqueueResponse(BaseModel):
    """Result of starting a scoring run."""

    job_id: str = Field(..., description="Run to open the event stream with")
    variants: list[str] = Field(default_factory=list, description="Variants it will score")
    rows: int = Field(default=0, ge=0, description="Questions each variant is asked")


# Union of everything the scoring stream can emit, for the docs module.
ScoreStreamEvent = (
    ScoreStartedEvent
    | ScoreProgressEvent
    | ScoreVariantEvent
    | ScoreErrorEvent
    | ScoreSummaryEvent
)
