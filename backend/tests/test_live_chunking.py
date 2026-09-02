"""The one test that talks to the real vendors. Off by default.

Everything else in the suite runs against fakes, which is what makes it fast
enough to run on every change — but a fake cannot notice the day Pinecone
renames the `namespace` argument, or OpenAI changes the shape of an embedding
response. That failure would reach production silently, so there is one test
that exercises the real thing:

    preview  ->  index a throwaway variant  ->  query it  ->  delete it

It is deliberately narrow. It uses a geometry nobody would create by hand
(`320/40`), so it cannot collide with a variant somebody is comparing, and it
removes what it made. It asserts production's vector count is unchanged, which
is the claim that would be most expensive to get wrong.

Run it with:

    uv run pytest --live tests/test_live_chunking.py

It costs a handful of embeddings and needs real credentials.
"""

import pytest

from app.config import settings
from app.schemas.chunking import ChunkStrategy, ChunkingConfig
from app.schemas.ingestion import IndexRequest
from app.services import (
    chunk_preview,
    chunk_variants,
    index_catalog,
    index_queue,
    retrieval,
    sync_status,
    vector_store,
)

pytestmark = pytest.mark.live

# A geometry no one would pick, so this test cannot disturb a real experiment.
CONFIG = ChunkingConfig(
    strategy=ChunkStrategy.BOUNDARY, chunk_size=320, chunk_overlap=40
)


@pytest.fixture
async def source_key() -> str:
    """A real, readable file from the real bucket."""
    statuses = await sync_status.list_statuses(refresh=True)

    readable = [
        status.source_key
        for status in statuses
        if status.source is not None and status.state != "unsupported"
    ]
    if not readable:
        pytest.skip("no readable file in the bucket to run against")

    return readable[0]


@pytest.fixture
async def throwaway_variant():
    """Yield the variant id, and drop it afterwards however the test ends."""
    identifier = chunk_variants.variant_id(CONFIG)
    try:
        yield identifier
    finally:
        await chunk_variants.delete(identifier)


async def test_the_real_round_trip(source_key, throwaway_variant):
    """Preview, index, query, and leave production exactly as it was."""
    before = vector_store.index_stats().get("total_vector_count", 0)

    # --- Preview: real bytes, real extractor, no writes -------------------
    preview = await chunk_preview.preview(source_key, CONFIG)

    assert preview.variant_id == throwaway_variant
    assert preview.stats.chunk_count > 0
    assert all(chunk.token_count <= CONFIG.chunk_size for chunk in preview.chunks)

    # --- Index into the variant's own namespace ---------------------------
    response = await index_queue.enqueue(
        IndexRequest(keys=[source_key], variant=throwaway_variant)
    )
    job = index_queue._jobs[response.job_id]
    if job.task is not None:
        await job.task

    run = index_queue.get(response.job_id)
    assert run is not None and run.state == "completed", run.error if run else "no run"

    described = await chunk_variants.describe(throwaway_variant)
    assert described is not None
    assert described.vector_count == preview.stats.chunk_count
    assert described.state == "ready"
    assert described.embedding_model == settings.embedding_model

    # --- Query it, and only it --------------------------------------------
    result = await retrieval.retrieve(
        "What does this document report?", top_k=3, variant=throwaway_variant
    )

    stored = await index_catalog.get_chunks(
        source_key, chunk_variants.space_for(throwaway_variant)
    )
    held = {chunk.vector_id for chunk in stored}

    assert result.chunks, "the variant returned nothing"
    assert {chunk.chunk_id for chunk in result.chunks} <= held

    # --- And production is untouched --------------------------------------
    assert vector_store.index_stats().get("total_vector_count", 0) == before
