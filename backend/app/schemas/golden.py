"""Golden set schemas — the evaluation questions generated from a source file.

A golden set is the answer key an eval run is scored against, so its shape is
fixed by `evals/run_eval.py` rather than chosen here: what `GoldenRow` exports
is exactly what the harness reads.  Fields absent from a row are omitted on
export rather than written as null, because the hand-written set omits them and
the two files must be interchangeable.

The fields the harness never sees — how a row was drafted, what the validator
found wrong with it, whether a person has accepted it — live on the same model
and are stripped at the boundary.  Keeping them together is what lets a flagged
row be shown with its reason instead of silently dropped.
"""

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SectionLevel(int, Enum):
    """How deep a heading sits in the document's outline."""

    # Text before the first heading — a cover page, a notice, an abstract.
    PREAMBLE = 0

    # A top-level heading. These are what a golden row cites in gold_sections.
    TOP = 1

    # A heading nested under a top-level one, kept for context, never cited.
    SUB = 2


class DocumentSection(BaseModel):
    """One titled span of a source document.

    The title is carried verbatim, never normalised or prettified, because a
    golden row's `gold_sections` must match it character for character for the
    harness's retrieval metrics to line up.
    """

    title: str = Field(..., description="Heading text exactly as it appears")

    level: SectionLevel = Field(
        default=SectionLevel.TOP, description="Where this heading sits in the outline"
    )

    # Everything under the heading, up to the next heading of the same or
    # shallower level.  The heading line itself is not repeated here.
    body: str = Field(default="", description="Text under this heading")

    # Titles of the headings nested inside this one.  Given to the drafting
    # prompt as an outline so it can see the shape of a long section without
    # being handed a second copy of the prose.
    subsections: list[str] = Field(
        default_factory=list, description="Titles of headings nested under this one"
    )

    start_line: int = Field(
        default=0, ge=0, description="Zero-based line where the heading appears"
    )

    end_line: int = Field(
        default=0, ge=0, description="Zero-based line after the section's last line"
    )

    @property
    def char_count(self) -> int:
        """How much prose this section holds, used to size its question quota."""
        return len(self.body)


class GoldenQuestionType(str, Enum):
    """What a question is testing.

    Closed, and served to the client rather than guessed at, for the same
    reason the evaluation tags are: a row filed under a type the harness does
    not bucket is a row nobody reads the score of.
    """

    # A single fact, stated outright in one section.
    LOOKUP = "lookup"

    # A fact that must be told apart from the same fact in another period.
    TEMPORAL = "temporal"

    # A fact sitting next to a near-identical wrong one.
    DISTRACTOR = "distractor"

    # A fact that must be joined across two sections.
    MULTI_HOP = "multi_hop"

    # A figure that must be computed, not quoted.
    ARITHMETIC = "arithmetic"

    # Several sentences that must be summarised.
    SYNTHESIS = "synthesis"

    # A fact the document does not state, where refusing is the right answer.
    UNANSWERABLE = "unanswerable"


class GoldenDifficulty(str, Enum):
    """How hard a row is expected to be, used to bucket the score."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class GoldenRowStatus(str, Enum):
    """Whether the validator could ground a row in the source document."""

    # Every check passed: the row can be trusted as an answer key.
    VALID = "valid"

    # At least one check failed. Kept and shown with its reasons rather than
    # discarded, because a flagged row is usually a good question with a bad
    # field, and deleting it silently would hide that.
    FLAGGED = "flagged"


class GoldenReview(str, Enum):
    """What a person decided about a row."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DROPPED = "dropped"


class GoldenSetState(str, Enum):
    """Where a set stands."""

    DRAFTING = "drafting"
    READY = "ready"
    FAILED = "failed"


class GoldenIssue(BaseModel):
    """One validator check a row did not pass."""

    check: str = Field(..., description="Name of the check, e.g. 'keys_verbatim'")
    detail: str = Field(..., description="What was wrong, naming the offending value")


class GoldenDerivation(BaseModel):
    """How an arithmetic row's answer was computed from figures in the document.

    Present so the validator can recompute the result rather than trust it.  A
    drafted number that appears nowhere in the source and cannot be derived
    from numbers that do is exactly the failure this whole feature exists to
    prevent, and this is what catches it.

    Never exported — the harness scores the answer, not the working.
    """

    operands: list[float] = Field(
        default_factory=list, description="Figures taken from the document, in order"
    )

    operator: str = Field(
        default="",
        description="How they combine: sum, difference, ratio, percent_of, or percent_change",
    )

    explanation: str = Field(
        default="", description="One line naming where each operand came from"
    )


class GoldenRow(BaseModel):
    """One question, its reference answer, and how to score an attempt at it.

    The exported subset of these fields is fixed by `evals/run_eval.py`.  The
    rest — the derivation, the validator's findings, the review decision — are
    how the row got here, and stop at the export boundary.
    """

    # Stable internal id, so editing a row does not depend on its Q-number,
    # which is reassigned whenever rows are added or dropped.
    row_id: str = Field(..., description="Internal id for this row")

    # The id the harness reads. Assigned by the store in document order, never
    # by the model — an LLM given the job produces collisions and gaps.
    question_id: str = Field(default="", description="Exported id, e.g. 'Q001'")

    type: GoldenQuestionType = Field(..., description="What this question tests")
    difficulty: GoldenDifficulty = Field(..., description="How hard it should be")

    question: str = Field(..., min_length=1, description="The question to ask")
    answer: str = Field(..., min_length=1, description="The reference answer")

    numeric_answer: Optional[float] = Field(
        default=None, description="Authoritative figure, when the answer is one"
    )

    numeric_tolerance: Optional[float] = Field(
        default=None, ge=0, description="How far off a figure may be and still pass"
    )

    answer_keys: list[str] = Field(
        default_factory=list,
        description="Strings that must appear in an answer, verbatim from the source",
    )

    forbidden_keys: list[str] = Field(
        default_factory=list,
        description="Strings that fail the row if present — the distractor's trap",
    )

    must_refuse: bool = Field(
        default=False, description="Whether the answer must decline to state this"
    )

    gold_sections: list[str] = Field(
        default_factory=list, description="Section titles the answer should come from"
    )

    note: str = Field(default="", description="The trap this question sets, if any")

    # --- Not exported ---

    derivation: Optional[GoldenDerivation] = Field(
        default=None, description="How an arithmetic answer was computed"
    )

    status: GoldenRowStatus = Field(
        default=GoldenRowStatus.VALID, description="Whether every check passed"
    )

    issues: list[GoldenIssue] = Field(
        default_factory=list, description="Checks this row did not pass"
    )

    review: GoldenReview = Field(
        default=GoldenReview.PENDING, description="What a person decided about this row"
    )

    edited: bool = Field(
        default=False, description="Whether a person has changed the drafted text"
    )


class GoldenSet(BaseModel):
    """A generated answer key for one source file."""

    set_id: str = Field(..., description="Id of this set")

    source_key: str = Field(..., description="Object key of the file it was drafted from")

    # Names the exported file. Defaults from the source stem and is editable,
    # because the file a person wants is 'meridian-fy2025.jsonl', not
    # '01-meridian-fy2025-annual-report.jsonl'.
    slug: str = Field(..., description="Filename stem used on export")

    state: GoldenSetState = Field(
        default=GoldenSetState.DRAFTING, description="Where this set stands"
    )

    provider: str = Field(default="", description="LLM provider that drafted it")
    model: str = Field(default="", description="Model that drafted it")

    created_at: Optional[datetime] = Field(default=None, description="When drafting began")
    updated_at: Optional[datetime] = Field(default=None, description="Last change")

    row_count: int = Field(default=0, ge=0, description="Rows drafted")
    valid_count: int = Field(default=0, ge=0, description="Rows that passed every check")
    accepted_count: int = Field(default=0, ge=0, description="Rows a person accepted")

    # Sections the splitter found, so a person editing gold_sections picks from
    # the real outline rather than typing a title that will never match.
    sections: list[str] = Field(
        default_factory=list, description="Section titles available to cite"
    )

    error: str = Field(default="", description="Why drafting failed, if it did")

    deleted: bool = Field(default=False, description="Whether this set was withdrawn")


class GoldenSetDetail(GoldenSet):
    """A set together with its rows."""

    rows: list[GoldenRow] = Field(default_factory=list, description="Every drafted row")


class GoldenRunRequest(BaseModel):
    """Body for starting a generation run."""

    source_key: str = Field(..., min_length=1, description="Object key to draft from")

    slug: str = Field(
        default="", max_length=120, description="Filename stem; derived from the key if blank"
    )

    provider: str = Field(default="", description="LLM provider; the configured default if blank")
    model: str = Field(default="", description="Model; the provider's default if blank")

    # Scales the per-section quota. The default is tuned so the Meridian report
    # lands near the 40 rows its hand-written set holds.
    density: float = Field(
        default=1.0, gt=0, le=3.0, description="Multiplier on the per-section question quota"
    )


class GoldenRowUpdate(BaseModel):
    """Body for editing or judging one drafted row.

    Every field is optional: a review decision and a text edit arrive through
    the same endpoint, and omitting a field leaves it alone.
    """

    type: Optional[GoldenQuestionType] = None
    difficulty: Optional[GoldenDifficulty] = None
    question: Optional[str] = Field(default=None, min_length=1, max_length=2_000)
    answer: Optional[str] = Field(default=None, min_length=1, max_length=8_000)
    numeric_answer: Optional[float] = None
    numeric_tolerance: Optional[float] = Field(default=None, ge=0)
    answer_keys: Optional[list[str]] = None
    forbidden_keys: Optional[list[str]] = None
    must_refuse: Optional[bool] = None
    gold_sections: Optional[list[str]] = None
    note: Optional[str] = Field(default=None, max_length=1_000)
    review: Optional[GoldenReview] = None


class GoldenOptions(BaseModel):
    """The question types and difficulties a client may choose from."""

    types: list[str] = Field(default_factory=list, description="Every question type")
    difficulties: list[str] = Field(default_factory=list, description="Every difficulty")
    checks: list[str] = Field(
        default_factory=list, description="Every validator check, so a client can label an issue"
    )


# --- Streamed events ---------------------------------------------------------
#
# Generation is a job, not a response: the POST returns a job id and progress
# arrives here, on a stream any client can reopen. Drafting a set costs a dozen
# model calls, and a closed tab must not throw that away.


class GoldenStage(str, Enum):
    """The stages a generation run moves through, in order."""

    # Fetch the file and decode it.
    EXTRACT = "extract"

    # Cut it into the sections a row is allowed to cite.
    SEGMENT = "segment"

    # Index every figure and name, so the validator has something to check against.
    FACTS = "facts"

    # Ask the model for questions.
    DRAFT = "draft"

    # Ground every drafted row in the source text.
    VALIDATE = "validate"

    # Score each row against its own answer, the way the harness will.
    SELF_CHECK = "self_check"


class GoldenStartedEventData(BaseModel):
    """Opens the stream: what this run is drafting from."""

    job_id: str = Field(default="", description="Id of the run this stream reports on")
    set_id: str = Field(default="", description="Set the run is filling")
    source_key: str = Field(default="", description="File being drafted from")
    model: str = Field(default="", description="Model doing the drafting")


class GoldenStartedEvent(BaseModel):
    """Sent once, when the run begins."""

    event: Literal["started"] = Field(default="started", description="The SSE event name")
    data: GoldenStartedEventData = Field(..., description="What this run will draft")


class GoldenStageEventData(BaseModel):
    """One stage finished, or one section within the drafting stage did."""

    stage: GoldenStage = Field(..., description="Stage that just advanced")
    detail: str = Field(default="", description="What it did, e.g. the section drafted")
    completed: int = Field(default=0, ge=0, description="Units done in this stage")
    total: int = Field(default=0, ge=0, description="Units this stage will do")


class GoldenStageEvent(BaseModel):
    """Progress within the run."""

    event: Literal["stage"] = Field(default="stage", description="The SSE event name")
    data: GoldenStageEventData = Field(..., description="Stage that advanced")


class GoldenRowEventData(BaseModel):
    """One row, as soon as it has been drafted and validated.

    Rows stream as they are ready rather than arriving together at the end, so
    a long run can be read while it is still going.
    """

    row: GoldenRow = Field(..., description="The row, with its validator findings")


class GoldenRowEvent(BaseModel):
    """A drafted row."""

    event: Literal["row"] = Field(default="row", description="The SSE event name")
    data: GoldenRowEventData = Field(..., description="The row that was drafted")


class GoldenErrorEventData(BaseModel):
    """A stage that failed.

    A failed section is reported and the run continues, because eleven good
    sections are worth more than an aborted run.
    """

    stage: GoldenStage = Field(..., description="Stage that failed")
    detail: str = Field(default="", description="Which section or pass failed")
    message: str = Field(..., description="Description of the failure")
    fatal: bool = Field(default=False, description="Whether the run stopped here")


class GoldenErrorEvent(BaseModel):
    """Something went wrong."""

    event: Literal["error"] = Field(default="error", description="The SSE event name")
    data: GoldenErrorEventData = Field(..., description="What failed, and whether it stopped")


class GoldenSummaryEventData(BaseModel):
    """Totals for the finished run."""

    set_id: str = Field(default="", description="Set that was filled")
    slug: str = Field(default="", description="Filename stem it will export under")
    row_count: int = Field(default=0, ge=0, description="Rows drafted")
    valid_count: int = Field(default=0, ge=0, description="Rows that passed every check")
    flagged_count: int = Field(default=0, ge=0, description="Rows needing a person's eye")
    by_type: dict[str, int] = Field(
        default_factory=dict, description="How many rows of each question type"
    )
    elapsed_ms: int = Field(default=0, ge=0, description="How long the run took")
    total_cost: float = Field(default=0.0, ge=0, description="What the drafting cost")


class GoldenSummaryEvent(BaseModel):
    """Sent once, when the run finishes."""

    event: Literal["summary"] = Field(default="summary", description="The SSE event name")
    data: GoldenSummaryEventData = Field(..., description="Totals for the whole run")


class GoldenRunState(str, Enum):
    """Where a generation run stands."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    # The process that owned it is gone, so it neither finished nor failed.
    ABANDONED = "abandoned"


class GoldenRun(BaseModel):
    """A generation run, as a client reopening the stream finds it."""

    job_id: str = Field(..., description="Id of this run")
    set_id: str = Field(default="", description="Set being filled")
    source_key: str = Field(default="", description="File being drafted from")
    state: GoldenRunState = Field(..., description="Where this run stands")
    stage: Optional[GoldenStage] = Field(default=None, description="Stage in flight")
    completed: int = Field(default=0, ge=0, description="Sections drafted so far")
    total: int = Field(default=0, ge=0, description="Sections to draft")
    row_count: int = Field(default=0, ge=0, description="Rows drafted so far")
    started_at: Optional[datetime] = Field(default=None, description="When it began")
    finished_at: Optional[datetime] = Field(default=None, description="When it ended")
    error: str = Field(default="", description="Failure detail, if it failed")

    # Where a reconnecting client resumes from, so nothing is replayed twice
    # and nothing missed while the tab was closed is lost.
    last_cursor: int = Field(default=0, ge=0, description="Highest event cursor emitted")


class GoldenEnqueueResponse(BaseModel):
    """What starting a run returns — an id to follow, never the work itself."""

    job_id: str = Field(..., description="Run to open the stream on")
    set_id: str = Field(..., description="Set the run will fill")
