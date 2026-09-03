"""OpenAPI documentation for the governance endpoints."""

GOVERNANCE_TAG = {
    "name": "governance",
    "description": (
        "The policy content is screened under. Governance stages run inside "
        "the indexing and chat pipelines — this surface only reads what they "
        "run under."
    ),
}

GOVERNANCE_POLICY_DESCRIPTION = """\
The resolved global governance policy — what every run uses when a request
sends nothing.

A client renders its defaults from this rather than hardcoding them: which
mode is in force (`off`, `audit_only`, `enforce`), what enforce does per
classification, and which of those fields a request may override per call
(`request_overridable`). The verbatim knob — how much of a matched value the
audit trail may hold — is reported but deliberately absent from
`request_overridable`: raw-value capture is an operator decision, configured
on the server only.

Where governance results appear:

* Indexing runs emit a `governance` event per screened file on the
  `/sources/index/events` stream, and a file refused outright arrives as an
  `error` event with stage `screening`.
* Chat emits a `governance` event after the question is screened and again
  after the answer is; a refusal ends the stream with a `blocked` event.
* All of them carry counts per entity type and class, never matched values.
"""

__all__ = ["GOVERNANCE_POLICY_DESCRIPTION", "GOVERNANCE_TAG"]
