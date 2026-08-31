"""Evaluation schemas — a judgement made on one traced chat request.

An evaluation is deliberately a *separate* record from the trace it judges, for
three reasons: a trace can be judged more than once, a judgement can be
withdrawn without destroying the evidence it was about, and a machine judge can
later write the same shape a person writes.

Each judgement names a `target` — retrieval or generation. That is what turns
"the answer was wrong" into "the answer was wrong *because the right chunk was
never retrieved*", which is the whole reason for keeping the chunks.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EvaluationTarget(str, Enum):
    """Which part of the pipeline a judgement is about."""

    # The chunks that were retrieved — were they the right evidence?
    RETRIEVAL = "retrieval"

    # The answer written from them — was it faithful to that evidence?
    GENERATION = "generation"

    # The exchange as a whole, for things neither stage owns.
    OVERALL = "overall"


class Verdict(str, Enum):
    """How well the target did."""

    GOOD = "good"
    PARTIAL = "partial"
    BAD = "bad"


class EvaluationAuthor(str, Enum):
    """Who made the judgement.

    Recorded from the start so an automated judge can be added later without
    a schema change — and so its scores never get mistaken for yours.
    """

    HUMAN = "human"
    LLM = "llm"
    CODE = "code"


class VerdictOption(BaseModel):
    """One selectable verdict, as offered to a client."""

    id: Verdict = Field(..., description="Value to send back on an evaluation")
    label: str = Field(..., description="Human-readable name")
    hint: str = Field(default="", description="What this verdict means, in plain terms")


class TagOption(BaseModel):
    """One selectable reason chip, scoped to the target it can explain."""

    id: str = Field(..., description="Value to send back in `tags`")
    label: str = Field(..., description="Human-readable name")
    target: EvaluationTarget = Field(..., description="Target this reason belongs to")
    hint: str = Field(default="", description="When to pick this one")


class EvaluationOptions(BaseModel):
    """The preset choices a client should build its evaluate control from.

    Served rather than hardcoded in the client for the same reason the model
    list is: the vocabulary is the backend's, and a client that invents its own
    tags produces evaluations nothing can group.
    """

    verdicts: list[VerdictOption] = Field(
        default_factory=list, description="Every verdict that may be given"
    )
    tags: list[TagOption] = Field(
        default_factory=list, description="Every reason chip, each scoped to a target"
    )
    targets: list[EvaluationTarget] = Field(
        default_factory=list, description="Targets that may be judged"
    )


class Evaluation(BaseModel):
    """One judgement on one trace."""

    id: str = Field(..., description="Id of this judgement")
    trace_id: str = Field(..., description="The trace being judged")

    target: EvaluationTarget = Field(..., description="Which stage this judges")
    verdict: Verdict = Field(..., description="How well that stage did")
    tags: list[str] = Field(
        default_factory=list, description="Preset reason ids explaining the verdict"
    )
    note: str = Field(default="", description="Free-text detail, if any")

    author: EvaluationAuthor = Field(
        default=EvaluationAuthor.HUMAN, description="Who or what judged it"
    )
    created_at: datetime = Field(..., description="When the judgement was made")

    # --- Withdrawal ---------------------------------------------------------
    # A withdrawn judgement is kept, not removed: the record of having changed
    # your mind is itself worth reading, and the trace it points at is evidence
    # that should never disappear because a verdict was retracted.

    deleted: bool = Field(default=False, description="Whether it has been withdrawn")
    deleted_at: Optional[datetime] = Field(
        default=None, description="When it was withdrawn"
    )
    deleted_reason: str = Field(default="", description="Why it was withdrawn")


class EvaluationRequest(BaseModel):
    """Request body for judging one stage of a trace."""

    target: EvaluationTarget = Field(
        default=EvaluationTarget.OVERALL, description="Which stage this judges"
    )
    verdict: Verdict = Field(..., description="How well that stage did")
    tags: list[str] = Field(
        default_factory=list, description="Preset reason ids from /evaluations/options"
    )
    note: str = Field(default="", max_length=4000, description="Free-text detail")
    author: EvaluationAuthor = Field(
        default=EvaluationAuthor.HUMAN, description="Who or what is judging"
    )


class EvaluationDeleteRequest(BaseModel):
    """Request body for withdrawing a judgement."""

    reason: str = Field(
        default="", max_length=500, description="Why it is being withdrawn"
    )


class EvaluationPage(BaseModel):
    """One page of judgements, newest first."""

    evaluations: list[Evaluation] = Field(
        default_factory=list, description="The page's rows"
    )
    total: int = Field(default=0, ge=0, description="Rows matching the filter in all")
    limit: int = Field(default=0, ge=0, description="Page size that was applied")
    offset: int = Field(default=0, ge=0, description="Rows skipped before this page")


__all__ = [
    "Evaluation",
    "EvaluationAuthor",
    "EvaluationDeleteRequest",
    "EvaluationOptions",
    "EvaluationPage",
    "EvaluationRequest",
    "EvaluationTarget",
    "TagOption",
    "Verdict",
    "VerdictOption",
]
