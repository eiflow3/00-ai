"""What the Sources screen says about a file once indexing moved off it.

The Index button is gone from Sources, so the screen's remaining job is to tell
you *where a file already is*: which namespaces hold it, whether each copy is
still current, and which of them is the one answering questions.

That listing has to be read back from the index rather than remembered, for the
same reason the variants table is: a namespace deleted on a console, or a run
that died halfway, must show as it really is and not as a job record claims.

Two failures are worth guarding specifically. A chip that stays green after the
file changed underneath it invites you to score a strategy on text that no
longer exists. And a status column still measuring the old production index
after production has been repointed would report a file as unindexed while the
app answers questions about it perfectly well.
"""

import httpx
import pytest

from app.schemas.source import IndexState
from app.main import app

REPLACEMENT = "MERIDIAN FREIGHTWORKS, INC.\n\nThis report has been withdrawn.\n"


@pytest.fixture
async def client(lab):
    """The app, with every vendor faked. `lab` is what makes that true."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as session:
        yield session


async def _row(client, source_key: str) -> dict:
    """One file's row from the sources listing."""
    listed = (await client.get("/sources")).json()
    match = [row for row in listed if row["source_key"] == source_key]

    assert match, f"{source_key} is not in the listing"
    return match[0]


async def test_a_file_lists_every_namespace_that_holds_it(
    client, report_key, index_variant
):
    """The chips are the point of the screen now: indexed where, and how much."""
    await index_variant(report_key, "recursive-512-64")
    await index_variant(report_key, "fixed-512-64")

    row = await _row(client, report_key)
    chips = {chip["variant_id"]: chip for chip in row["variants"]}

    assert set(chips) == {"recursive-512-64", "fixed-512-64"}
    assert chips["recursive-512-64"]["label"] == "recursive · 512/64"
    assert all(chip["chunk_count"] > 0 for chip in chips.values())
    assert all(chip["state"] == IndexState.CURRENT.value for chip in chips.values())


async def test_a_file_nobody_has_indexed_lists_none(client, report_key):
    """An empty chip list and `not_indexed` are the same fact, told twice."""
    row = await _row(client, report_key)

    assert row["variants"] == []
    assert row["state"] == IndexState.NOT_INDEXED.value


async def test_the_original_index_appears_as_a_chip_of_its_own(
    client, report_key, index_variant
):
    """Production is one namespace among the rest, and reads as one."""
    await index_variant(report_key)

    row = await _row(client, report_key)

    assert [chip["variant_id"] for chip in row["variants"]] == [""]
    assert row["variants"][0]["chunk_count"] > 0


async def test_a_chip_goes_stale_when_the_file_changes_underneath_it(
    client, objects, report_key, index_variant
):
    """A variant holding the previous draft must not read as current.

    Changed in the bucket, not through the app — which is the case a job record
    would miss entirely.
    """
    await index_variant(report_key, "recursive-512-64")
    objects.put(report_key, REPLACEMENT.encode("utf-8"))

    row = await _row(client, report_key)
    chip = row["variants"][0]

    assert chip["variant_id"] == "recursive-512-64"
    assert chip["state"] == IndexState.STALE_CONTENT.value


async def test_a_half_embedded_copy_is_marked_on_its_own_chip(
    client, report_key, index_variant
):
    """One broken namespace must not make the other three look broken."""
    from app.services import chunk_variants, vector_store

    await index_variant(report_key, "recursive-512-64")
    await index_variant(report_key, "fixed-512-64")

    space = chunk_variants.space_for("fixed-512-64")
    ids = vector_store.list_vector_ids("", space)
    vector_store.delete_vectors(ids[-1:], space)

    row = await _row(client, report_key)
    chips = {chip["variant_id"]: chip["state"] for chip in row["variants"]}

    assert chips["fixed-512-64"] == IndexState.INTERRUPTED.value
    assert chips["recursive-512-64"] == IndexState.CURRENT.value


async def test_the_chip_production_answers_from_is_flagged(
    client, report_key, index_variant
):
    """So "which of these am I actually talking to" is answerable at a glance."""
    await index_variant(report_key, "recursive-512-64")
    await index_variant(report_key, "fixed-512-64")
    await client.put("/chunking/production", json={"variant_id": "fixed-512-64"})

    row = await _row(client, report_key)
    active = [chip["variant_id"] for chip in row["variants"] if chip["active"]]

    assert active == ["fixed-512-64"]


async def test_the_status_column_follows_the_pointer(
    client, report_key, index_variant
):
    """The verdict is about the space that answers, not about a fixed index.

    Left measuring the original index, this column would call a file unindexed
    while every answer on the Chat screen was grounded in it.
    """
    await index_variant(report_key, "recursive-512-64")

    assert (await _row(client, report_key))["state"] == IndexState.NOT_INDEXED.value

    await client.put("/chunking/production", json={"variant_id": "recursive-512-64"})

    assert (await _row(client, report_key))["state"] == IndexState.CURRENT.value


async def test_deleting_a_file_clears_it_from_every_chip(
    client, report_key, index_variant
):
    """Chips cannot outlive the file, or they would offer text nobody can open."""
    await index_variant(report_key, "recursive-512-64")
    await index_variant(report_key, "fixed-512-64")

    deleted = await client.delete(f"/sources/{report_key}")
    assert deleted.status_code == 200

    listed = (await client.get("/sources")).json()

    assert [row for row in listed if row["source_key"] == report_key] == []


async def test_a_run_can_target_a_variant_without_naming_files(client, report_key):
    """The bulk sweep the Sources button used to own, now aimed at a namespace.

    Without this, moving indexing to the Chunking screen would quietly cost the
    only way to index a whole bucket in one action.
    """
    from app.services import chunk_variants, index_queue

    response = await client.post(
        "/sources/index", json={"variant": "recursive-512-64", "only_stale": True}
    )

    assert response.status_code == 202
    assert report_key in response.json()["accepted"]

    job = index_queue._jobs[response.json()["job_id"]]
    if job.task is not None:
        await job.task

    described = await chunk_variants.describe("recursive-512-64")

    assert described is not None
    assert described.source_keys == [report_key]


async def test_a_bulk_sweep_reindexes_a_variant_that_has_gone_stale(
    client, objects, report_key, index_variant
):
    """Staleness has to be judged per namespace, or the sweep skips the work.

    Measured against production alone, a variant holding the old draft looks
    up to date and is never re-cut.
    """
    from app.services import chunk_variants, index_queue

    await index_variant(report_key, "recursive-512-64")
    objects.put(report_key, REPLACEMENT.encode("utf-8"))

    response = await client.post(
        "/sources/index", json={"variant": "recursive-512-64", "only_stale": True}
    )

    assert response.json()["accepted"] == [report_key]

    job = index_queue._jobs[response.json()["job_id"]]
    if job.task is not None:
        await job.task

    row = await _row(client, report_key)
    chips = {chip["variant_id"]: chip["state"] for chip in row["variants"]}

    assert chips["recursive-512-64"] == IndexState.CURRENT.value
