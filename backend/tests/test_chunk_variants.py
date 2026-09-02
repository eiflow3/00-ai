"""A variant's identity, and the precedence rules around it.

Pure and fast: no vendor is involved in deciding what a variant is called or
where it lives. That is the point of keeping those rules in one module — they
can be checked without a network, and every caller inherits the answer.

The round trip is the property that matters most. The id is the namespace, so an
id that does not read back into the configuration that produced it would mean a
namespace whose name lies about its contents, and nothing downstream could
detect it.
"""

import pytest

from app.config import settings
from app.schemas.chunking import ChunkStrategy, ChunkingConfig
from app.services import chunk_variants
from app.services.chunk_variants import UnknownVariant


def test_the_id_reads_back_into_the_configuration():
    """An id is the namespace, so it has to name its own contents exactly."""
    for strategy in ChunkStrategy:
        config = ChunkingConfig(strategy=strategy, chunk_size=256, chunk_overlap=32)
        identifier = chunk_variants.variant_id(config)

        assert identifier == f"{strategy.value}-256-32"
        assert chunk_variants.parse(identifier) == config


def test_geometry_is_part_of_the_identity():
    """Two geometries are two experiments, and must not share vectors."""
    recursive = ChunkStrategy.RECURSIVE

    coarse = chunk_variants.variant_id(
        ChunkingConfig(strategy=recursive, chunk_size=512, chunk_overlap=64)
    )
    fine = chunk_variants.variant_id(
        ChunkingConfig(strategy=recursive, chunk_size=256, chunk_overlap=32)
    )

    assert coarse != fine
    assert chunk_variants.space_for(coarse).namespace != (
        chunk_variants.space_for(fine).namespace
    )


def test_production_is_the_empty_variant():
    """Naming nothing means the index the app answers from, not a lab space."""
    space = chunk_variants.space_for(chunk_variants.PRODUCTION_VARIANT)

    assert space.namespace == ""
    assert space.index == settings.pinecone_index_name


def test_a_variant_lives_in_its_own_namespace_in_the_lab_index():
    """The isolation the whole feature rests on, as an addressing property."""
    space = chunk_variants.space_for("structural-512-64")

    assert space.index == settings.pinecone_lab_index_name
    assert space.index != settings.pinecone_index_name
    assert space.namespace == "structural-512-64"


@pytest.mark.parametrize(
    "identifier",
    ["nonsense", "recursive", "recursive-512", "recursive-512-x", "wobbly-512-64"],
)
def test_an_unreadable_variant_is_refused_rather_than_guessed_at(identifier):
    """Guessing would create a namespace on first write and make it real.

    A variant named after a strategy that no longer exists holds vectors nothing
    can reproduce, so relabelling it would make a stale experiment look current.
    """
    with pytest.raises(UnknownVariant):
        chunk_variants.space_for(identifier)


def test_a_named_variant_overrides_the_requested_geometry():
    """The precedence rule, settled in one place and checked here.

    A request naming both a variant and a chunk size is contradictory. Honouring
    the size would produce vectors in a namespace whose name describes something
    else, which no later read could detect.
    """
    fallback = ChunkingConfig(
        strategy=ChunkStrategy.BOUNDARY, chunk_size=512, chunk_overlap=64
    )

    config, variant = chunk_variants.resolve("recursive-256-32", fallback)

    assert variant == "recursive-256-32"
    assert config.strategy is ChunkStrategy.RECURSIVE
    assert (config.chunk_size, config.chunk_overlap) == (256, 32)


def test_naming_no_variant_keeps_the_requested_geometry():
    """The ordinary indexing path, which must be untouched by any of this."""
    fallback = ChunkingConfig(
        strategy=ChunkStrategy.FIXED, chunk_size=1024, chunk_overlap=128
    )

    config, variant = chunk_variants.resolve("", fallback)

    assert variant == chunk_variants.PRODUCTION_VARIANT
    assert config == fallback


def test_the_label_is_written_for_a_person():
    """It appears on screen and in log lines, so it is worth pinning."""
    config = ChunkingConfig(
        strategy=ChunkStrategy.RECURSIVE, chunk_size=512, chunk_overlap=64
    )

    assert chunk_variants.label_for(config) == "recursive · 512/64"


async def test_production_cannot_be_deleted_as_a_variant():
    """The guard between "drop an experiment" and "empty the live index"."""
    with pytest.raises(UnknownVariant):
        await chunk_variants.delete(chunk_variants.PRODUCTION_VARIANT)
