"""Actions, policy knobs, runner and audit contract for governance stages.

Written BEFORE the implementation (see tests/GOVERNANCE_TEST_PLAN.md): the red
suite is the specification.  The load-bearing claims:

  * **Actions are surgical** — masking changes the matched span and nothing
    else, and a pseudonym is stable across documents, or retrieval quietly
    breaks the moment the same person appears in two files.
  * **The mode knob is three-valued** — off (no cost, stamped unscreened),
    audit_only (byte-identical output, findings recorded), enforce (acted on).
  * **Precedence is request > source > global default**, resolved in one
    place, so a call site never reasons about layering.
  * **Enforce fails closed** — a crashed detector must halt the pipeline, not
    wave content through; under audit_only the same crash is advisory.
  * **The audit record never re-leaks** — what the verbatim knob allows into
    a record is the whole privacy boundary of the feature.
"""

import pytest

from app.schemas.governance import (
    EntityType,
    Finding,
    GovernanceMode,
    GovernancePolicy,
    PiiClass,
    VerbatimMode,
)
from app.services.governance import audit, policy as policy_service, runner
from app.services.governance.pii import actions, detector

EMAIL_TEXT = "email mc.reyes.demo@gmail.com or call (202) 555-0143 anytime"


def findings_for(text: str) -> list[Finding]:
    return detector.detect(text)


def policy(**overrides) -> GovernancePolicy:
    return GovernancePolicy(**overrides)


# -------------------------------------------------------------------- actions


def test_mask_replaces_the_span_and_nothing_else():
    masked = actions.mask(EMAIL_TEXT, findings_for(EMAIL_TEXT))

    assert "mc.reyes.demo@gmail.com" not in masked
    assert "(202) 555-0143" not in masked
    assert "[EMAIL]" in masked and "[PHONE]" in masked
    # Everything outside the spans is byte-identical.
    assert masked.startswith("email ") and masked.endswith(" anytime")


def test_pseudonyms_are_stable_across_calls_and_documents():
    """The same person in two files must land on the same placeholder, or a
    query about them retrieves only one of the files."""
    first = actions.pseudonymize("mc.reyes.demo@gmail.com")
    second = actions.pseudonymize("mc.reyes.demo@gmail.com")
    other = actions.pseudonymize("juan.delacruz@acmecorp.example")

    assert first == second
    assert first != other
    assert "mc.reyes" not in first


# --------------------------------------------------------------- policy knobs


def test_precedence_is_request_over_source_over_default():
    default = policy(mode=GovernanceMode.ENFORCE)
    source = policy(mode=GovernanceMode.AUDIT_ONLY)
    request = policy(mode=GovernanceMode.OFF)

    assert policy_service.resolve(default=default).mode is GovernanceMode.ENFORCE
    assert policy_service.resolve(default=default, source=source).mode is GovernanceMode.AUDIT_ONLY
    assert policy_service.resolve(default=default, source=source, request=request).mode is GovernanceMode.OFF


async def test_mode_off_runs_no_detector_and_stamps_unscreened(monkeypatch):
    """Off means no detection cost paid — not detection with the result
    thrown away."""
    def exploding_detect(text):
        raise AssertionError("detector must not run when mode is off")

    monkeypatch.setattr(detector, "detect", exploding_detect)

    result = await runner.run(EMAIL_TEXT, policy(mode=GovernanceMode.OFF))

    assert result.output_text == EMAIL_TEXT
    assert result.screened is False
    assert result.findings == []


async def test_audit_only_records_but_never_touches_the_text():
    result = await runner.run(EMAIL_TEXT, policy(mode=GovernanceMode.AUDIT_ONLY))

    assert result.output_text == EMAIL_TEXT  # byte-identical
    assert result.screened is True
    assert result.findings, "audit_only still detects and records"


async def test_enforce_applies_the_configured_action():
    result = await runner.run(EMAIL_TEXT, policy(mode=GovernanceMode.ENFORCE))

    assert "mc.reyes.demo@gmail.com" not in result.output_text
    assert result.findings


async def test_skipped_stage_still_appears_in_the_timeline():
    """A chat client must be able to see the request ran unscreened."""
    result = await runner.run(EMAIL_TEXT, policy(mode=GovernanceMode.OFF))

    assert any(stage.status == "skipped" for stage in result.stages)


# ----------------------------------------------------------------- fail modes


async def test_enforce_fails_closed_on_a_detector_crash(monkeypatch):
    """Unscreened content leaking through a crashed stage defeats the whole
    point — under enforce the pipeline halts."""
    def crash(text):
        raise RuntimeError("recognizer blew up")

    monkeypatch.setattr(detector, "detect", crash)

    with pytest.raises(runner.GovernanceStageFailure):
        await runner.run(EMAIL_TEXT, policy(mode=GovernanceMode.ENFORCE))


async def test_audit_only_survives_a_detector_crash(monkeypatch):
    """Screening was advisory, so the crash is logged and content proceeds."""
    def crash(text):
        raise RuntimeError("recognizer blew up")

    monkeypatch.setattr(detector, "detect", crash)

    result = await runner.run(EMAIL_TEXT, policy(mode=GovernanceMode.AUDIT_ONLY))

    assert result.output_text == EMAIL_TEXT
    assert any(stage.status == "failed" for stage in result.stages)


# ---------------------------------------------------------------- audit shape


def a_finding() -> Finding:
    return Finding(
        entity_type=EntityType.EMAIL,
        text="mc.reyes.demo@gmail.com",
        start=6,
        end=29,
        classification=PiiClass.PERSONAL,
        confidence=0.95,
    )


def test_verbatim_off_stores_no_raw_value_anywhere():
    record = audit.shape(a_finding(), VerbatimMode.OFF)

    assert "mc.reyes.demo@gmail.com" not in str(record)
    assert record["entity_type"] == EntityType.EMAIL.value
    assert record["start"] == 6 and record["end"] == 29


def test_verbatim_preview_stores_a_masked_excerpt_only():
    record = audit.shape(a_finding(), VerbatimMode.PREVIEW)

    assert "mc.reyes.demo@gmail.com" not in str(record)
    assert "*" in record["preview"]


def test_verbatim_full_stores_the_value_and_stamps_the_mode():
    record = audit.shape(a_finding(), VerbatimMode.FULL)

    assert record["value"] == "mc.reyes.demo@gmail.com"
    assert record["verbatim"] == VerbatimMode.FULL.value


def test_the_verbatim_knob_is_not_a_request_field():
    """Callers of chat/ingest must not be able to switch raw capture on:
    the request-level policy schema simply has no such field."""
    assert "verbatim" not in type(policy()).request_overridable_fields()
