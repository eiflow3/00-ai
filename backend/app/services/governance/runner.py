"""Runs the governance stages for one piece of content.

The only module a pipeline imports.  The mode knob decides everything else:

  * OFF — no detector executes (no detection cost paid), and the result is
    stamped unscreened with a `skipped` timeline entry, so a client can see
    the request ran without screening.
  * AUDIT_ONLY — detect and classify, but the output text is byte-identical
    to the input; findings exist only for the audit trail.
  * ENFORCE — the policy's action is applied per finding, and a crashed
    detector FAILS CLOSED: unscreened content leaking through a broken stage
    defeats the whole feature, so the pipeline halts.  Under AUDIT_ONLY the
    same crash is advisory — logged, marked failed, content proceeds.

Detection is synchronous CPU work, so it runs under `asyncio.to_thread` and
never blocks the event loop.
"""

import asyncio
import logging
import time

from app.schemas.governance import (
    Finding,
    GovernanceMode,
    GovernancePolicy,
    GovernanceResult,
    StageOutcome,
)

from .pii import actions, classifier, detector

logger = logging.getLogger(__name__)

STAGE_NAME = "governance.pii"


class GovernanceStageFailure(RuntimeError):
    """A stage crashed under enforce; content must not pass unscreened."""


class GovernanceBlocked(RuntimeError):
    """Content was refused by policy — a verdict, not a malfunction.

    Raised by pipelines (not by `run`, which reports the verdict on its
    result) when a blocked document has to unwind out of a generator.
    """


async def run(text: str, policy: GovernancePolicy) -> GovernanceResult:
    """Screen one piece of content under the resolved policy."""
    if policy.mode is GovernanceMode.OFF:
        logger.info("governance stage=%s skipped: mode is off", STAGE_NAME)
        return GovernanceResult(
            output_text=text,
            findings=[],
            screened=False,
            stages=[StageOutcome(name=STAGE_NAME, status="skipped")],
        )

    started = time.perf_counter()
    try:
        findings: list[Finding] = await asyncio.to_thread(detector.detect, text)
        findings = await asyncio.to_thread(classifier.classify, text, findings, policy)
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        if policy.mode is GovernanceMode.ENFORCE:
            logger.error("governance stage=%s failed closed: %s", STAGE_NAME, exc)
            raise GovernanceStageFailure(f"{STAGE_NAME} failed under enforce") from exc
        logger.error(
            "governance stage=%s failed (advisory under audit_only): %s",
            STAGE_NAME, exc,
        )
        return GovernanceResult(
            output_text=text,
            findings=[],
            screened=False,
            stages=[
                StageOutcome(name=STAGE_NAME, status="failed", duration_ms=duration_ms)
            ],
        )

    edits = []
    if policy.mode is GovernanceMode.AUDIT_ONLY:
        output, verdict = text, "allowed"
    else:
        output, verdict, edits = actions.apply(text, findings, policy)

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "governance stage=%s mode=%s findings=%d verdict=%s duration_ms=%.1f",
        STAGE_NAME, policy.mode.value, len(findings), verdict, duration_ms,
    )
    return GovernanceResult(
        output_text=output,
        edits=edits,
        findings=findings,
        screened=True,
        verdict=verdict,
        stages=[
            StageOutcome(name=STAGE_NAME, status="succeeded", duration_ms=duration_ms)
        ],
    )
