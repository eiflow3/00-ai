"""Production as a pointer rather than a place.

Production used to be a location: its own index, written only by the Sources
screen, and the thing `/chat` fell back to.  That made "which cut answers my
questions" a property of where bytes were written, so changing it meant
re-embedding the corpus.

It is now a *pointer* — one stored variant id naming the namespace the app
answers from.  Winning a scoreboard is therefore a decision you can act on:
point production at the winner and the next answer comes from it, with nothing
re-embedded and nothing copied.

The tests here are about the three ways that can go wrong:

  * the pointer is ignored, and the app quietly answers from the old place;
  * the pointer is allowed to name something that cannot answer — an empty
    namespace, a half-embedded one, an id no strategy can reproduce;
  * the pointer is lost on restart, which would silently revert every answer.
"""

import httpx
import pytest

from app.main import app
from app.schemas.chunking import VariantState
from app.services import answer_space, chunk_variants, retrieval, vector_store

QUESTION = "What did Meridian report for freight revenue?"


@pytest.fixture
async def client(lab):
    """The app, with every vendor faked. `lab` is what makes that true."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as session:
        yield session


async def test_production_starts_out_as_the_original_index(client):
    """No pointer stored is not an error — it is the shipped default.

    An install that has never run an experiment must answer from exactly where
    it always did, so the absence of a row has to mean the original index and
    not "unconfigured".
    """
    response = await client.get("/chunking/production")

    assert response.status_code == 200
    body = response.json()

    assert body["variant_id"] == ""
    assert body["label"]
    assert await answer_space.current() == chunk_variants.PRODUCTION_VARIANT


async def test_an_answer_comes_from_wherever_production_points(
    client, report_key, index_variant
):
    """The whole point: repointing changes the answer with nothing re-embedded.

    Before the pointer moves, the original index is empty and retrieval finds
    nothing — which is the honest state, not a fallback. After it moves, the
    same call reads the variant's vectors without a single write in between.
    """
    await index_variant(report_key, "recursive-512-64")

    before = await retrieval.retrieve(QUESTION, top_k=3)
    assert not before.chunks, "the original index should still be empty"

    pointed = await client.put(
        "/chunking/production", json={"variant_id": "recursive-512-64"}
    )
    assert pointed.status_code == 200
    assert pointed.json()["variant_id"] == "recursive-512-64"

    after = await retrieval.retrieve(QUESTION, top_k=3)

    assert after.chunks, "production should now answer from the variant"

    held = {
        chunk.vector_id
        for chunk in await _chunks_in(report_key, "recursive-512-64")
    }
    assert {chunk.chunk_id for chunk in after.chunks} <= held


async def test_naming_a_variant_still_overrides_the_pointer(
    client, report_key, index_variant
):
    """The B column of a comparison must not follow production's choice."""
    await index_variant(report_key, "recursive-512-64")
    await index_variant(report_key, "fixed-512-64")
    await client.put("/chunking/production", json={"variant_id": "recursive-512-64"})

    result = await retrieval.retrieve(QUESTION, top_k=3, variant="fixed-512-64")

    held = {chunk.vector_id for chunk in await _chunks_in(report_key, "fixed-512-64")}

    assert result.chunks
    assert {chunk.chunk_id for chunk in result.chunks} <= held


async def test_pointing_at_a_namespace_that_holds_nothing_is_refused(client):
    """A pointer at an empty namespace is an app that answers nothing at all.

    Refused rather than accepted-and-reported, because the failure would only
    show up as an ungrounded answer much later.
    """
    response = await client.put(
        "/chunking/production", json={"variant_id": "structural-512-64"}
    )

    assert response.status_code == 409
    assert "structural-512-64" in response.json()["detail"]
    assert await answer_space.current() == chunk_variants.PRODUCTION_VARIANT


async def test_pointing_at_an_id_no_strategy_can_run_is_a_bad_request(client):
    """Rejected on the id alone, before anything looks for a namespace."""
    response = await client.put(
        "/chunking/production", json={"variant_id": "telepathy-512-64"}
    )

    assert response.status_code == 400
    assert await answer_space.current() == chunk_variants.PRODUCTION_VARIANT


async def test_pointing_at_a_half_embedded_variant_is_refused(
    client, report_key, index_variant
):
    """A variant missing chunks answers from a document with holes in it.

    The scoreboard already refuses to score one; production has more at stake,
    because nothing about the answer would look wrong.
    """
    await index_variant(report_key, "fixed-512-64")

    space = chunk_variants.space_for("fixed-512-64")
    ids = vector_store.list_vector_ids("", space)
    vector_store.delete_vectors(ids[-1:], space)

    described = await chunk_variants.describe("fixed-512-64")
    assert described is not None and described.state == VariantState.INTERRUPTED

    response = await client.put(
        "/chunking/production", json={"variant_id": "fixed-512-64"}
    )

    assert response.status_code == 409
    assert await answer_space.current() == chunk_variants.PRODUCTION_VARIANT


async def test_the_pointer_survives_a_restart(client, report_key, index_variant):
    """It is configuration, so it outlives the process that set it.

    Held in a local database rather than in memory: a pointer that reset on
    restart would silently move every answer back to the old index.
    """
    await index_variant(report_key, "recursive-512-64")
    await client.put("/chunking/production", json={"variant_id": "recursive-512-64"})

    answer_space.close()

    assert await answer_space.current() == "recursive-512-64"


async def test_production_can_be_pointed_back_at_the_original_index(
    client, report_key, index_variant
):
    """Repointing is reversible, or nobody would try it in the first place."""
    await index_variant(report_key)
    await index_variant(report_key, "recursive-512-64")

    await client.put("/chunking/production", json={"variant_id": "recursive-512-64"})
    back = await client.put("/chunking/production", json={"variant_id": ""})

    assert back.status_code == 200
    assert back.json()["variant_id"] == ""
    assert await answer_space.current() == chunk_variants.PRODUCTION_VARIANT


async def test_deleting_the_variant_production_answers_from_is_refused(
    client, report_key, index_variant
):
    """Otherwise one click leaves the app with nothing to answer from.

    Repointing first is a deliberate act; a delete that silently emptied
    production would not be.
    """
    await index_variant(report_key, "recursive-512-64")
    await client.put("/chunking/production", json={"variant_id": "recursive-512-64"})

    response = await client.delete("/chunking/variants/recursive-512-64")

    assert response.status_code == 409
    described = await chunk_variants.describe("recursive-512-64")
    assert described is not None and described.vector_count > 0


async def test_a_pointer_at_a_namespace_that_has_since_gone_reports_itself(
    client, report_key, index_variant
):
    """Emptied from the Pinecone console, the pointer must say so.

    It is not corrected automatically: silently reverting to the old index
    would answer from a different corpus than the one the screen names.
    """
    await index_variant(report_key, "recursive-512-64")
    await client.put("/chunking/production", json={"variant_id": "recursive-512-64"})

    await chunk_variants.delete("recursive-512-64")

    body = (await client.get("/chunking/production")).json()

    assert body["variant_id"] == "recursive-512-64"
    assert body["state"] == VariantState.MISSING.value
    assert body["vector_count"] == 0


async def test_the_pointer_names_the_files_it_can_answer_about(
    client, report_key, index_variant
):
    """What the Chat header reads from, so a wrong corpus is visible up front."""
    await index_variant(report_key, "recursive-512-64")
    await client.put("/chunking/production", json={"variant_id": "recursive-512-64"})

    body = (await client.get("/chunking/production")).json()

    assert body["source_keys"] == [report_key]
    assert body["vector_count"] > 0
    assert body["state"] == VariantState.READY.value
    assert body["label"] == "recursive · 512/64"


async def test_going_back_is_only_offered_while_there_is_somewhere_to_go(
    client, report_key, index_variant
):
    """The original index can be retired, and then reverting cannot work.

    The screen builds its "back to the original index" button off this count,
    so a zero here is what stops it offering an action that would be refused.
    """
    await index_variant(report_key, "recursive-512-64")
    await client.put("/chunking/production", json={"variant_id": "recursive-512-64"})

    assert (await client.get("/chunking/production")).json()["original_vector_count"] == 0

    # And once the original index does hold something, it is offered again.
    await index_variant(report_key)

    body = (await client.get("/chunking/production")).json()

    assert body["variant_id"] == "recursive-512-64"
    assert body["original_vector_count"] > 0


async def _chunks_in(source_key: str, variant: str):
    """The chunks one variant holds for a file."""
    from app.services import index_catalog

    return await index_catalog.get_chunks(
        source_key, chunk_variants.space_for(variant)
    )
