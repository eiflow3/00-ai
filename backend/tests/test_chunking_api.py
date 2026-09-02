"""The HTTP surface a client actually talks to.

Driven through the app itself rather than by calling the services, because the
things that break here are the things a router owns: which failures become which
status code, and whether a payload matches the shape the frontend types expect.

The two status codes worth guarding are both about *when* a mistake is
reported. A bad variant has to fail the request, not the run — once an SSE
stream has opened its status code has already been sent, and the only remaining
way to report a problem is an event the client may well treat as best-effort.
"""

import httpx
import pytest

from app.main import app
from app.services.chunking import registry


@pytest.fixture
async def client(lab):
    """The app, with every vendor faked. `lab` is what makes that true."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as session:
        yield session


async def test_the_catalog_describes_every_strategy(client):
    """The picker is built from this, so a missing field is a blank tile."""
    response = await client.get("/chunking/strategies")

    assert response.status_code == 200
    offered = response.json()

    assert {spec["id"] for spec in offered} == {
        strategy.value for strategy in registry.strategies()
    }
    assert all(spec["label"] and spec["summary"] for spec in offered)
    # The overlap control is disabled off this flag, so it has to be present.
    assert all("honours_overlap" in spec for spec in offered)


async def test_a_preview_costs_nothing(client, lab, report_key):
    """The claim printed on the button: nothing embedded, nothing written."""
    response = await client.post(
        "/chunking/preview",
        json={
            "source_key": report_key,
            "config": {"strategy": "structural", "chunk_size": 512, "chunk_overlap": 64},
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["variant_id"] == "structural-512-64"
    assert body["label"] == "structural · 512/64"
    assert body["stats"]["chunk_count"] == len(body["chunks"])
    assert body["stats"]["chunk_count"] > 0
    assert all(chunk["token_count"] <= 512 for chunk in body["chunks"])

    # Not one vector was written anywhere, in any index the request could have
    # touched. This is the assertion that keeps "free" honest.
    assert all(
        index.describe_index_stats()["total_vector_count"] == 0
        for index in lab.values()
    )


async def test_a_preview_reports_the_shape_of_the_cut(client, report_key):
    """Those six numbers are what a person compares strategies on."""
    response = await client.post(
        "/chunking/preview",
        json={
            "source_key": report_key,
            "config": {"strategy": "fixed", "chunk_size": 512, "chunk_overlap": 64},
        },
    )

    stats = response.json()["stats"]

    assert stats["min_tokens"] <= stats["median_tokens"] <= stats["max_tokens"]
    assert stats["document_tokens"] > 0
    assert stats["total_tokens"] >= stats["document_tokens"]
    assert 0 <= stats["repeated_fraction"] < 1


async def test_an_impossible_geometry_is_a_bad_request(client, report_key):
    """Reported as 400 rather than raised as a 500 from inside the splitter."""
    response = await client.post(
        "/chunking/preview",
        json={
            "source_key": report_key,
            "config": {"strategy": "boundary", "chunk_size": 128, "chunk_overlap": 128},
        },
    )

    assert response.status_code == 400
    assert "chunk_overlap" in response.json()["detail"]


async def test_previewing_a_file_that_is_not_there_is_a_404(client):
    """Distinguished from a file that cannot be read, which is a 415."""
    response = await client.post(
        "/chunking/preview",
        json={"source_key": "no-such-file.txt", "config": {"strategy": "boundary"}},
    )

    assert response.status_code == 404


async def test_an_unknown_strategy_is_rejected_by_the_schema(client, report_key):
    """The enum is the contract, so an invented strategy never reaches a service."""
    response = await client.post(
        "/chunking/preview",
        json={"source_key": report_key, "config": {"strategy": "telepathy"}},
    )

    assert response.status_code == 422


async def test_variants_are_listed_after_indexing(client, report_key, index_variant):
    """What the variants table renders, and the `Ask` button is built from."""
    assert (await client.get("/chunking/variants")).json() == []

    await index_variant(report_key, "recursive-512-64")

    listed = (await client.get("/chunking/variants")).json()

    assert len(listed) == 1
    assert listed[0]["variant_id"] == "recursive-512-64"
    assert listed[0]["label"] == "recursive · 512/64"
    assert listed[0]["source_keys"] == [report_key]
    assert listed[0]["state"] == "ready"
    assert listed[0]["vector_count"] > 0
    assert listed[0]["config"]["strategy"] == "recursive"


async def test_deleting_a_variant_reports_what_it_removed(
    client, report_key, index_variant
):
    """And deleting one that is already gone is the state the caller asked for."""
    await index_variant(report_key, "fixed-512-64")

    first = await client.delete("/chunking/variants/fixed-512-64")
    again = await client.delete("/chunking/variants/fixed-512-64")

    assert first.status_code == 200
    assert first.json()["deleted"] > 0
    assert again.status_code == 200
    assert again.json()["deleted"] == 0


async def test_deleting_a_variant_that_could_never_exist_is_a_bad_request(client):
    """The guard that keeps an unvalidated id away from a namespace-wide delete."""
    response = await client.delete("/chunking/variants/not-a-variant")

    assert response.status_code == 400


async def test_indexing_into_an_unrunnable_variant_is_a_bad_request(
    client, report_key
):
    """Caught before the run starts, so a typo does not create a namespace.

    The run reports progress over SSE, so by the time a stream exists its status
    code has been sent — validation has to happen on the request that enqueues.
    """
    response = await client.post(
        "/sources/index",
        json={"keys": [report_key], "variant": "recursive-512"},
    )

    assert response.status_code == 400
    assert "recursive-512" in response.json()["detail"]


async def test_indexing_into_a_variant_is_accepted(client, report_key):
    """The ordinary path: `202` and a run id to stream from."""
    response = await client.post(
        "/sources/index",
        json={"keys": [report_key], "variant": "structural-512-64"},
    )

    assert response.status_code == 202
    body = response.json()

    assert body["accepted"] == [report_key]
    assert body["job_id"]


async def test_asking_from_an_unrunnable_variant_is_a_bad_request(client):
    """Reported before the stream opens, or it could only be an `error` event.

    Which retrieval treats as best-effort and answers around, so a mistyped
    variant would read as a model that had somehow lost its context.
    """
    response = await client.post(
        "/chat",
        json={"query": "anything at all", "chunk_variant": "structural-512"},
    )

    assert response.status_code == 400


async def test_scoring_against_a_set_that_does_not_exist_is_a_404(client):
    """Distinguished from a set that exists but has nothing to score with."""
    response = await client.post(
        "/chunking/score", json={"set_id": "00000000-0000-0000-0000-000000000000"}
    )

    assert response.status_code == 404


async def test_stopping_a_scoring_run_that_is_not_running_is_a_404(client):
    """A score is held in memory only, so a run that aged out is simply gone."""
    response = await client.delete("/chunking/score/deadbeefdeadbeef")

    assert response.status_code == 404
