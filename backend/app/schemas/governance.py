"""Shapes for governance stages: findings, policy, and stage results.

The contract these types carry (pinned by tests/GOVERNANCE_TEST_PLAN.md):

  * A `Finding` holds the raw matched text **in process only** — what may be
    persisted or streamed is decided by the verbatim knob at the audit
    boundary (`services/governance/audit.py`), never here.
  * `GovernancePolicy` layers: a global default, an optional per-source
    policy, and a per-request override — resolved in
    `services/governance/policy.py`.  Only the fields named by
    `request_overridable_fields` may arrive from a request body, which is
    how the verbatim knob stays out of callers' hands.
"""

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class EntityType(str, Enum):
    """What kind of thing was detected. PERSON_NAME, ADDRESS and
    DATE_OF_BIRTH are reserved for the NER tier — the regex tier does not
    claim them."""

    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    IP_ADDRESS = "ip_address"
    PERSON_NAME = "person_name"
    ADDRESS = "address"
    DATE_OF_BIRTH = "date_of_birth"


class PiiClass(str, Enum):
    """Whose data it is — decided by the classifier, never by shape alone."""

    PERSONAL = "personal"
    BUSINESS = "business"
    AMBIGUOUS = "ambiguous"
    INFRA = "infra"


class GovernanceMode(str, Enum):
    """The three-valued knob: off pays no detection cost, audit_only records
    without touching the text, enforce acts on findings."""

    OFF = "off"
    AUDIT_ONLY = "audit_only"
    ENFORCE = "enforce"


class VerbatimMode(str, Enum):
    """How much of a matched value the audit record may hold."""

    OFF = "off"
    PREVIEW = "preview"
    FULL = "full"


class GovernanceAction(str, Enum):
    """What enforce does to a finding of a given class."""

    MASK = "mask"
    PSEUDONYMIZE = "pseudonymize"
    TAG = "tag"
    REJECT = "reject"


class Finding(BaseModel):
    """One detected entity, located by offsets into the screened text."""

    entity_type: EntityType = Field(..., description="What kind of entity matched")
    text: str = Field(..., description="The raw matched span — in process only")
    start: int = Field(..., ge=0, description="Offset of the match in the input text")
    end: int = Field(..., ge=0, description="End offset of the match")
    classification: Optional[PiiClass] = Field(
        default=None, description="Whose data it is; None until classified"
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Detector confidence in (0, 1]"
    )


def _default_actions() -> dict[PiiClass, GovernanceAction]:
    """Personal data is masked; everything else is kept and tagged so a
    retrieval-time policy can still act on it."""
    return {
        PiiClass.PERSONAL: GovernanceAction.MASK,
        PiiClass.AMBIGUOUS: GovernanceAction.TAG,
        PiiClass.BUSINESS: GovernanceAction.TAG,
        PiiClass.INFRA: GovernanceAction.TAG,
    }


class GovernancePolicy(BaseModel):
    """One resolved rulebook for a governance run."""

    mode: GovernanceMode = Field(
        default=GovernanceMode.ENFORCE, description="off / audit_only / enforce"
    )
    verbatim: VerbatimMode = Field(
        default=VerbatimMode.PREVIEW,
        description="How much of a matched value audit records may hold",
    )
    own_domains: list[str] = Field(
        default_factory=list,
        description="Domains whose addresses classify as business",
    )
    actions: dict[PiiClass, GovernanceAction] = Field(
        default_factory=_default_actions,
        description="What enforce does per classification",
    )

    @classmethod
    def request_overridable_fields(cls) -> frozenset[str]:
        """The only fields a request body may override.  The verbatim knob is
        deliberately absent: raw-value capture is a config/source decision,
        never a caller's."""
        return frozenset({"mode"})


class GovernanceFindingSummary(BaseModel):
    """One line of what a governance run found — counts only, never values.

    This is the shape that crosses a wire (SSE events, run history): entity
    type, class, what was done, and how many. The raw matched text never
    appears here — echoing it back would re-leak what was just redacted.
    """

    entity_type: EntityType = Field(..., description="What kind of entity matched")
    classification: Optional[PiiClass] = Field(
        default=None, description="Whose data it was judged to be"
    )
    action: GovernanceAction = Field(..., description="What was done about it")
    count: int = Field(..., ge=1, description="How many findings this line covers")


class GovernancePolicyView(BaseModel):
    """The resolved global policy, as GET /governance/policy reports it.

    What a client reads to render its defaults — which mode requests run
    under when they send nothing, and which fields a request may override.
    """

    mode: GovernanceMode = Field(..., description="Default mode for every run")
    verbatim: VerbatimMode = Field(
        ..., description="How much of a matched value audit records hold"
    )
    own_domains: list[str] = Field(
        default_factory=list, description="Domains classified as business"
    )
    actions: dict[PiiClass, GovernanceAction] = Field(
        default_factory=dict, description="What enforce does per classification"
    )
    request_overridable: list[str] = Field(
        default_factory=list, description="Policy fields a request body may override"
    )
    stages: list[str] = Field(
        default_factory=list, description="Governance stages this deployment runs"
    )


class StageOutcome(BaseModel):
    """One stage's entry in the run timeline — mirrors the chat `stage`
    event, so a skipped governance stage is still visible to a client."""

    name: str = Field(..., description="Machine-readable stage id")
    status: Literal["succeeded", "failed", "skipped"] = Field(
        ..., description="How the stage ended"
    )
    duration_ms: float = Field(
        default=0.0, ge=0.0, description="How long the stage took"
    )


class SpanEdit(BaseModel):
    """One replacement enforce made: which span, and how long it is now.

    Carried so a caller that tracks offsets into the text (page spans, most
    importantly) can shift them instead of silently mis-attributing every
    page after the first redaction.
    """

    start: int = Field(..., ge=0, description="Start offset in the original text")
    end: int = Field(..., ge=0, description="End offset in the original text")
    new_length: int = Field(..., ge=0, description="Length of the replacement")


class GovernanceResult(BaseModel):
    """What a governance run hands back to the pipeline that called it."""

    output_text: str = Field(..., description="The text the pipeline continues with")
    edits: list[SpanEdit] = Field(
        default_factory=list,
        description="Replacements applied to the text, in offset order",
    )
    findings: list[Finding] = Field(
        default_factory=list, description="Everything detected, classified"
    )
    screened: bool = Field(
        ..., description="False when mode=off stamped the run unscreened"
    )
    verdict: Literal["allowed", "blocked"] = Field(
        default="allowed", description="blocked when policy rejected the content"
    )
    stages: list[StageOutcome] = Field(
        default_factory=list, description="Timeline entries, one per stage"
    )
