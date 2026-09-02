"""The promise the feature is built on: four cuts of one file, kept apart.

Every test here drives the *real* pipeline — the queue, the plan, the ingestion
loop, the vector-store calls — against a fake index that honours namespaces.
That is deliberate: the isolation is not something the app enforces, it is
something it delegates, so the only useful test is one where the delegation
actually happens.

Four claims are made in the docs and the UI, and each one is a test below:

  * indexing under four variants writes four separate sets of vectors;
  * production is untouched by any of it;
  * a query against one variant cannot see another's chunks;
  * deleting one variant leaves the others alone, and deleting the *file*
    leaves nothing behind anywhere.
"""

from app.config import settings
from app.schemas.chunking import ChunkStrategy, ChunkingConfig
from app.services import chunk_preview, chunk_variants, retrieval
from app.services.chunking import registry
from app.services.retrieval import EmbeddingModelMismatch  # noqa: F401  (documents the guard)


# The four variants a person creates by pressing "Index all four".
VARIANTS = [f"{strategy.value}-512-64" for strategy in registry.strategies()]

# A question whose answer sits in one identifiable section of the report.
QUESTION = "What was Cold Chain segment revenue and how much did it grow?"


async def test_indexing_four_variants_writes_four_separate_sets(lab, report_key, index_variant):
    """One file, four cuts, four namespaces — and none of them production."""
    for variant in VARIANTS:
        run = await index_variant(report_key, variant)
        assert run.state == "completed", run.error
        assert run.indexed == 1

    laboratory = lab[settings.pinecone_lab_index_name]

    assert set(laboratory.namespaces) == set(VARIANTS)
    assert all(laboratory.namespaces[variant] for variant in VARIANTS)

    # And production holds nothing. A handle for it does get built — every run
    # re-reads each file's status when it closes, which reads the live index —
    # but no vector is ever written there.
    production = lab.get(settings.pinecone_index_name)
    assert production is None or production.describe_index_stats()["total_vector_count"] == 0


async def test_each_variant_holds_exactly_what_its_preview_promised(
    lab, report_key, index_variant
):
    """The preview is what a person decides on, so it has to be the truth.

    A preview that under-reported would understate the embedding bill; one that
    over-reported would make a complete variant look interrupted.
    """
    for strategy in registry.strategies():
        variant = f"{strategy.value}-512-64"

        preview = await chunk_preview.preview(
            report_key,
            ChunkingConfig(strategy=strategy, chunk_size=512, chunk_overlap=64),
        )
        await index_variant(report_key, variant)

        described = await chunk_variants.describe(variant)

        assert described is not None
        assert described.vector_count == preview.stats.chunk_count
        assert described.chunk_total == preview.stats.chunk_count
        assert described.state == "ready"
        assert described.source_keys == [report_key]


async def test_a_query_cannot_cross_out_of_its_variant(lab, report_key, index_variant):
    """The claim a person is relying on when they compare two answers."""
    for variant in VARIANTS:
        await index_variant(report_key, variant)

    laboratory = lab[settings.pinecone_lab_index_name]

    for variant in VARIANTS:
        result = await retrieval.retrieve(QUESTION, top_k=5, variant=variant)
        returned = {chunk.chunk_id for chunk in result.chunks}
        held = set(laboratory.namespaces[variant])

        assert returned, f"{variant} returned nothing at all"
        assert returned <= held, f"{variant} returned ids it does not hold"

        # And nothing from any other variant, which is the same claim from the
        # other side — a vector id is a *slot*, so two variants hold overlapping
        # ids and only the namespace tells them apart.
        for other in VARIANTS:
            if other == variant:
                continue
            foreign = {
                vector_id
                for vector_id in laboratory.namespaces[other]
                if vector_id not in held
            }
            assert not (returned & foreign)


async def test_variants_do_not_leak_into_a_production_answer(lab, report_key, index_variant):
    """Nothing done in the lab may change what the app normally answers."""
    for variant in VARIANTS:
        await index_variant(report_key, variant)

    result = await retrieval.retrieve(QUESTION, top_k=5)

    assert result.chunks == []
    assert result.total_searched == 0


async def test_production_and_a_variant_hold_the_file_at_the_same_time(lab, report_key, index_variant):
    """The ordinary case while an experiment is running: both are live."""
    await index_variant(report_key)
    await index_variant(report_key, "structural-512-64")

    production = await retrieval.retrieve(QUESTION, top_k=3)
    experiment = await retrieval.retrieve(QUESTION, top_k=3, variant="structural-512-64")

    assert production.chunks and experiment.chunks

    # The same question, cut two ways: the text that comes back differs, which
    # is the entire reason for comparing them.
    assert [chunk.content for chunk in production.chunks] != [
        chunk.content for chunk in experiment.chunks
    ]

    # Every chunk still traces back to the file it came from, in both.
    assert {chunk.source for chunk in production.chunks} == {report_key}
    assert {chunk.source for chunk in experiment.chunks} == {report_key}


async def test_the_right_section_comes_back_from_the_structural_variant(lab, report_key, index_variant):
    """A weak but real retrieval check, so isolation is not the only claim.

    Similarity here is term overlap rather than meaning, which is enough for
    this: the section that literally discusses Cold Chain revenue should be the
    closest match to a question about Cold Chain revenue. If retrieval were
    wired to the wrong namespace, or the metadata lost its text, this is what
    would notice.
    """
    await index_variant(report_key, "structural-512-64")

    result = await retrieval.retrieve(QUESTION, top_k=3, variant="structural-512-64")

    # Within the top few rather than first: similarity here is term overlap, not
    # meaning, so pinning an exact rank would be testing the fake rather than
    # the pipeline.
    assert any("Cold Chain" in chunk.content for chunk in result.chunks)


async def test_re_indexing_a_variant_embeds_nothing(lab, report_key, index_variant):
    """Resuming has to work per variant, or a comparison costs double.

    The plan compares against what *that* variant holds. Comparing against
    production — or against another variant — would re-embed everything every
    time, and the only symptom would be the bill.
    """
    first = await index_variant(report_key, "recursive-512-64")
    again = await index_variant(report_key, "recursive-512-64")

    assert first.total_reused == 0
    assert again.total_reused > 0
    assert again.indexed == 1


async def test_one_run_can_cover_several_variants(lab, report_key, index_variant):
    """"Index all four" is one run, and each entry keeps its own terms.

    The geometry used to be fixed when a run started, so a second variant
    queued behind the first would have been embedded the first one's way — four
    copies of the same thing, and a comparison of nothing.
    """
    from app.schemas.ingestion import IndexRequest
    from app.services import index_queue

    # Queued one after another without waiting, which is what four clicks in a
    # row do.
    responses = [
        await index_queue.enqueue(IndexRequest(keys=[report_key], variant=variant))
        for variant in VARIANTS
    ]

    assert len({response.job_id for response in responses}) == 1, "not one run"

    job = index_queue._jobs[responses[0].job_id]
    if job.task is not None:
        await job.task

    laboratory = lab[settings.pinecone_lab_index_name]

    assert set(laboratory.namespaces) == set(VARIANTS)

    # Each namespace holds a different number of vectors, which is the proof
    # they were each cut their own way rather than four times the same way.
    counts = {len(laboratory.namespaces[variant]) for variant in VARIANTS}
    assert len(counts) > 1, "every variant produced the same chunk count"


async def test_the_same_file_and_variant_is_not_queued_twice(lab, report_key, index_variant):
    """Two clicks on one variant is one piece of work; on two, it is two."""
    from app.schemas.ingestion import IndexRequest
    from app.services import index_queue, index_registry

    first = await index_queue.enqueue(
        IndexRequest(keys=[report_key], variant="fixed-512-64")
    )
    repeat = await index_queue.enqueue(
        IndexRequest(keys=[report_key], variant="fixed-512-64")
    )
    other = await index_queue.enqueue(
        IndexRequest(keys=[report_key], variant="boundary-512-64")
    )

    assert repeat.already_queued == [report_key]
    assert other.accepted == [report_key]

    job = index_queue._jobs[first.job_id]
    if job.task is not None:
        await job.task

    assert index_registry.pending() == []


async def test_deleting_one_variant_leaves_the_others(lab, report_key, index_variant):
    """Dropping an experiment must not disturb the ones being compared to it."""
    for variant in VARIANTS:
        await index_variant(report_key, variant)

    removed = await chunk_variants.delete("fixed-512-64")

    assert removed > 0
    assert await chunk_variants.describe("fixed-512-64") is None

    remaining = {variant.variant_id for variant in await chunk_variants.list_variants()}
    assert remaining == set(VARIANTS) - {"fixed-512-64"}


async def test_deleting_the_file_leaves_nothing_behind_anywhere(lab, report_key, index_variant):
    """A deleted file's chunks are still retrievable until they are gone.

    Which is worse in a variant than in production: the text is perfectly
    findable, so a comparison run would score four strategies against a document
    nobody can look up any more.
    """
    from app.services import deletion

    await index_variant(report_key)
    for variant in VARIANTS:
        await index_variant(report_key, variant)

    deleted, removed = await deletion.delete_source(report_key)

    assert removed is True
    assert deleted > 0
    assert await chunk_variants.list_variants() == []

    laboratory = lab[settings.pinecone_lab_index_name]
    assert laboratory.describe_index_stats()["total_vector_count"] == 0


async def test_deindexing_leaves_the_experiments_alone(lab, report_key, index_variant):
    """Withdrawing a file from retrieval is not the same as deleting it.

    It leaves the file re-indexable, so the experiments built from it are still
    about a document that exists.
    """
    from app.services import deletion

    await index_variant(report_key)
    await index_variant(report_key, "structural-512-64")

    await deletion.delete_vectors(report_key)

    assert await chunk_variants.describe("structural-512-64") is not None
    assert await retrieval.retrieve(QUESTION, top_k=3) is not None


async def test_a_half_written_variant_reports_itself_interrupted(lab, report_key, index_variant):
    """A variant missing vectors must never be scored as though it were whole.

    Simulated the only way it happens in life: a run that wrote some of the
    file's chunks and stopped. Every chunk carries the total the file should
    have, so the count disagreeing with it is the evidence.
    """
    await index_variant(report_key, "recursive-512-64")

    laboratory = lab[settings.pinecone_lab_index_name]
    space = laboratory.namespaces["recursive-512-64"]

    # Drop the last chunk, as an interrupted write would have left it.
    space.pop(sorted(space)[-1])

    described = await chunk_variants.describe("recursive-512-64")

    assert described is not None
    assert described.state == "interrupted"
    assert described.vector_count < described.chunk_total


async def test_a_foreign_namespace_is_not_claimed_as_a_variant(lab, report_key, index_variant):
    """Something else's namespace in the lab index is not ours to describe.

    Inventing a configuration for it would put a row on screen that no strategy
    can reproduce, and hand a namespace-wide delete an id nobody validated.
    """
    await index_variant(report_key, "boundary-512-64")

    laboratory = lab[settings.pinecone_lab_index_name]
    laboratory.upsert(
        [{"id": "x#00000", "values": [0.0] * 64, "metadata": {}}],
        namespace="somebody-elses-work",
    )

    listed = {variant.variant_id for variant in await chunk_variants.list_variants()}

    assert listed == {"boundary-512-64"}
