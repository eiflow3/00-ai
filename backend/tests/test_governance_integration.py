"""Governance wired into the pipelines it exists for: indexing and chat.

The unit suites (test_governance_detection / test_governance_runner) prove the
module's contract in isolation. What only these tests can prove is the claim
the feature was built on:

  * **Nothing the policy redacts reaches the index.** Asserted against the
    fake vector store, so it covers everything between the screening stage
    and the upsert — chunking included.
  * **Off means off, and it is stamped.** The content goes in raw, and the
    run's own event stream says `screened: false`, so an unscreened index is
    never silently ambiguous.
  * **Chat never puts a detected value on the wire.** Under enforce the raw
    email appears nowhere in the whole SSE stream — not in the retrieval
    echo, not in a stage detail, not in a delta.
  * **A blocked question never reaches the provider.** The adapter records
    whether it was called; policy refusing the question must end the stream
    before any LLM spend.

The LLM adapter is faked at the same seam philosophy as the vendors in
conftest: the router resolves it through `get_adapter`, so patching that one
binding leaves the whole streaming path real.
"""

from typing import Optional

import httpx
import pytest

from app.main import app
from app.schemas.governance import (
    GovernanceAction,
    GovernanceMode,
    GovernancePolicy,
    PiiClass,
)
from app.services.governance import policy as governance_policy

# The cast every assertion keys on. Synthetic, reserved ranges only.
EMAIL = "mc.reyes.demo@gmail.com"
SSN = "000-12-3456"

PII_KEY = "hr/pii-memo.txt"
PII_TEXT = (
    "Relocation memo. Maria's personal email is "
    f"{EMAIL} and the government ID on file is {SSN}. "
    "The Manila office expansion continues through 2027 with the "
    "capacity policy obliging additional commissioning within four quarters."
)


@pytest.fixture
async def client(lab):
    """The app over ASGI, every vendor faked."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as session:
        yield session


@pytest.fixture
def pii_source(objects) -> str:
    """A file with known PII, stored in the fake bucket."""
    objects.put(PII_KEY, PII_TEXT.encode("utf-8"))
    return PII_KEY


class FakeAdapter:
    """An LLM that answers with a fixed string and remembers being called."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.called = False
        self.usage = None

    async def stream(self, messages, model, temperature):
        self.called = True
        # Split mid-value on purpose: a screening that only looked at single
        # deltas would miss an email broken across two of them.
        half = len(self.answer) // 2
        yield self.answer[:half]
        yield self.answer[half:]


@pytest.fixture
def adapter(monkeypatch) -> FakeAdapter:
    """Answer chat with text that contains the same PII as the memo."""
    fake = FakeAdapter(f"Sure — reach Maria at {EMAIL} about the move.")
    monkeypatch.setattr("app.routers.chat.get_adapter", lambda provider: fake)
    return fake


def stored_text(lab) -> str:
    """Every chunk the fake indexes hold, as one searchable string."""
    return " ".join(
        str(record.metadata)
        for index in lab.values()
        for space in index.namespaces.values()
        for record in space.values()
    )


def run_events(job_id: str) -> list:
    """The typed events a run emitted, straight from its live buffer."""
    from app.services import index_queue

    return [event for _, event in index_queue._jobs[job_id].events]


def reject_personal() -> GovernancePolicy:
    """A default policy that refuses personal data outright."""
    return GovernancePolicy(
        actions={
            PiiClass.PERSONAL: GovernanceAction.REJECT,
            PiiClass.AMBIGUOUS: GovernanceAction.TAG,
            PiiClass.BUSINESS: GovernanceAction.TAG,
            PiiClass.INFRA: GovernanceAction.TAG,
        }
    )


# ------------------------------------------------------------------ indexing


async def test_enforce_keeps_pii_out_of_the_index(lab, pii_source, index_variant):
    """The feature's founding claim: whatever enters the index is published
    to anyone who can query it, so ingest is where redaction must hold."""
    run = await index_variant(pii_source)

    assert run.indexed == 1
    stored = stored_text(lab)
    assert EMAIL not in stored
    assert SSN not in stored
    assert "[EMAIL]" in stored and "[SSN]" in stored


async def test_off_indexes_raw_and_stamps_the_run(lab, pii_source, index_variant):
    """Off is the operator's right — but the run must say it was exercised."""
    run = await index_variant(pii_source, governance_mode="off")

    assert EMAIL in stored_text(lab)

    stamps = [
        event.data
        for event in run_events(run.job_id)
        if getattr(event, "event", "") == "governance"
    ]
    assert len(stamps) == 1
    assert stamps[0].screened is False
    assert stamps[0].mode is GovernanceMode.OFF


async def test_audit_only_records_without_touching(lab, pii_source, index_variant):
    """Same raw index as off, but the findings exist for the audit trail."""
    run = await index_variant(pii_source, governance_mode="audit_only")

    assert EMAIL in stored_text(lab)

    stamps = [
        event.data
        for event in run_events(run.job_id)
        if getattr(event, "event", "") == "governance"
    ]
    assert stamps[0].screened is True
    assert sum(line.count for line in stamps[0].findings) >= 2  # email + ssn


async def test_a_rejected_file_leaves_no_vectors(
    lab, pii_source, index_variant, monkeypatch
):
    """Blocked means nothing of the file was embedded — not one chunk."""
    monkeypatch.setattr(governance_policy, "default_policy", reject_personal)

    run = await index_variant(pii_source)

    assert run.indexed == 0
    assert stored_text(lab) == ""

    events = run_events(run.job_id)
    stamps = [e.data for e in events if getattr(e, "event", "") == "governance"]
    assert stamps[0].verdict == "blocked"
    refusals = [
        e.data
        for e in events
        if getattr(e, "event", "") == "error" and e.data.stage == "screening"
    ]
    assert refusals, "a refused file must say so on the stream"


# ---------------------------------------------------------------------- chat


def frames(raw: str) -> list[dict]:
    """Parse an SSE body into [{event, data}] — enough for assertions."""
    parsed: list[dict] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        event, data = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data.append(line.split(":", 1)[1].strip())
        if data:
            parsed.append({"event": event, "data": "\n".join(data)})
    return parsed


QUESTION = f"My personal email is {EMAIL} — when does the Manila office open?"


async def test_chat_enforce_never_puts_pii_on_the_wire(client, adapter):
    """Not in the retrieval echo, not in a stage detail, not in a delta."""
    response = await client.post(
        "/chat", json={"query": QUESTION, "use_rag": False}
    )

    assert response.status_code == 200
    assert EMAIL not in response.text
    assert "[EMAIL]" in response.text

    events = frames(response.text)
    screenings = [f for f in events if f["event"] == "governance"]
    assert len(screenings) == 2  # the question, then the answer
    assert adapter.called  # the (masked) question did reach the model


async def test_chat_off_streams_raw_and_says_so(client, adapter):
    """Unscreened is allowed; invisible is not."""
    response = await client.post(
        "/chat", json={"query": QUESTION, "use_rag": False, "governance_mode": "off"}
    )

    assert EMAIL in response.text

    screenings = [f for f in frames(response.text) if f["event"] == "governance"]
    assert len(screenings) == 2
    assert all('"screened":false' in f["data"] for f in screenings)


async def test_a_blocked_question_never_reaches_the_model(
    client, adapter, monkeypatch
):
    """Policy refusing the question must cost nothing and answer nothing."""
    monkeypatch.setattr(governance_policy, "default_policy", reject_personal)

    response = await client.post(
        "/chat", json={"query": QUESTION, "use_rag": False}
    )

    events = frames(response.text)
    assert any(f["event"] == "blocked" for f in events)
    assert not adapter.called
    # Terminal: nothing after the block, so no message deltas at all.
    assert not [f for f in events if f["event"] == "message"]


async def test_the_policy_endpoint_reports_the_defaults(client):
    """The client renders its knobs from this, not from hardcoded values."""
    response = await client.get("/governance/policy")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "enforce"
    assert body["request_overridable"] == ["mode"]
    assert "verbatim" not in body["request_overridable"]
    assert body["stages"] == ["governance.pii"]