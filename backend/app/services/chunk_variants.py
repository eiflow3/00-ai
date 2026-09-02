"""Variants — one chunking configuration, and the vectors it produced.

A *variant* is a strategy plus the geometry it ran at: `recursive · 512/64`.
It is the unit of comparison, because a strategy on its own is not one — the
same splitter at 512 tokens and at 256 retrieves differently enough to be a
separate experiment, so it gets separate vectors.

This module owns every rule connecting a variant to where its vectors live,
the way app.services.provenance owns the rules connecting a source file to its
vector ids.  Nothing else spells out a namespace, and nothing else knows which
index the experiments are in.

Two decisions are worth stating because they are what make the comparison
trustworthy:

  * **A variant is a namespace, not a metadata tag.**  A query issued against
    one namespace cannot return another's vectors — the isolation is the vector
    store's, not a filter every call site has to remember.
  * **The variant list is read back from the index.**  There is no table of
    experiments to fall out of step with reality: what exists is whatever holds
    vectors, so a restart, a console deletion or a run that died halfway all
    show the truth.

The identifier is readable on purpose — `recursive-512-64`, not a hash.  It is
the namespace in the Pinecone console, it appears in log lines, and it can be
read back into the configuration that produced it without consulting anything.
"""

import asyncio
import logging
from typing import Optional

from app.config import settings
from app.schemas.chunking import (
    ChunkStrategy,
    ChunkVariant,
    ChunkingConfig,
    VariantState,
)
from app.services import index_catalog
from app.services.embeddings import EMBEDDING_MODEL_METADATA_KEY
from app.services.provenance import (
    METADATA_CHUNK_TOTAL,
    METADATA_EMBEDDED_AT,
    METADATA_SOURCE_KEY,
    parse_vector_id,
    to_datetime,
    vector_id_for,
)
from app.services.vector_store import (
    VectorSpace,
    delete_namespace,
    fetch_vectors,
    list_vector_ids,
    namespace_stats,
)

logger = logging.getLogger(__name__)

# The empty variant is production: the index the app answers from normally.
# Named rather than written as "" at each call site, because "no variant" is a
# real choice a client makes and not a missing value.
PRODUCTION_VARIANT = ""

# How a variant id is built and read back.
VARIANT_SEPARATOR = "-"

# Between the parts of the name a person reads.
LABEL_SEPARATOR = " · "

# The chunk whose metadata describes the whole file — position 0 carries the
# source key, the expected chunk total and the embed time.
DESCRIBING_CHUNK = 0


class UnknownVariant(ValueError):
    """Raised when a variant id does not name a configuration we can run."""


def variant_id(config: ChunkingConfig) -> str:
    """Build the id — and therefore the namespace — for a configuration.

    Args:
        config: The strategy and geometry.

    Returns:
        An id of the form "recursive-512-64", stable for that configuration so
        re-indexing it writes over the same vectors instead of a second copy.
    """
    return VARIANT_SEPARATOR.join(
        (config.strategy.value, str(config.chunk_size), str(config.chunk_overlap))
    )


def parse(identifier: str) -> ChunkingConfig:
    """Read a variant id back into the configuration that produced it.

    Args:
        identifier: An id previously produced by `variant_id`.

    Returns:
        The configuration it names.

    Raises:
        UnknownVariant: If the id is malformed or names a strategy that no
            longer exists. Raised rather than guessed at — a variant whose
            strategy has been removed holds vectors nothing can reproduce, and
            quietly relabelling it would make a stale experiment look current.
    """
    strategy, _, geometry = identifier.partition(VARIANT_SEPARATOR)
    size, _, overlap = geometry.partition(VARIANT_SEPARATOR)

    if not size.isdigit() or not overlap.isdigit():
        raise UnknownVariant(f"Not a variant id: {identifier!r}")

    try:
        return ChunkingConfig(
            strategy=ChunkStrategy(strategy),
            chunk_size=int(size),
            chunk_overlap=int(overlap),
        )
    except ValueError as exc:
        raise UnknownVariant(f"Not a variant id: {identifier!r} — {exc}") from exc


def label_for(config: ChunkingConfig) -> str:
    """Name a variant the way it should read on screen and in a log line."""
    return f"{config.strategy.value}{LABEL_SEPARATOR}{config.chunk_size}/{config.chunk_overlap}"


def space_for(identifier: str = PRODUCTION_VARIANT) -> VectorSpace:
    """Return the vector space a variant's chunks live in.

    The single place the mapping from an experiment to storage is decided. It
    is a namespace inside the lab index today; giving one variant an index of
    its own would be a change here and nowhere else.

    Args:
        identifier: The variant id, or empty for production.

    Returns:
        The space to read and write in.

    Raises:
        UnknownVariant: If the id is not one this app can run.
    """
    if not identifier:
        return VectorSpace()

    # Validated rather than trusted: an unparseable id would otherwise create a
    # namespace on first write and quietly become a real experiment.
    parse(identifier)

    return VectorSpace(
        index_name=settings.pinecone_lab_index_name, namespace=identifier
    )


def resolve(
    variant: str, fallback: ChunkingConfig
) -> tuple[ChunkingConfig, str]:
    """Settle which configuration a request means and where it writes.

    A named variant carries its own strategy and geometry, so it wins outright
    and whatever else the request said about chunk size is ignored — the point
    of a variant is that its name fully determines its contents, and honouring
    a conflicting size would produce vectors the name lies about.

    Args:
        variant: The variant id a caller named, or empty for production.
        fallback: The configuration to use when no variant was named.

    Returns:
        The configuration to cut with, and the variant to write to.

    Raises:
        UnknownVariant: If the id is not one this app can run.
    """
    if variant:
        return parse(variant), variant
    return fallback, PRODUCTION_VARIANT


def _documents(ids: list[str]) -> dict[str, list[str]]:
    """Group a namespace's vector ids by the document each belongs to."""
    grouped: dict[str, list[str]] = {}
    for vector_id in ids:
        document_id, index = parse_vector_id(vector_id)
        if index is None:
            continue
        grouped.setdefault(document_id, []).append(vector_id)
    return grouped


async def describe(identifier: str) -> Optional[ChunkVariant]:
    """Read back what one variant holds.

    Args:
        identifier: The variant id.

    Returns:
        The variant, or None when it holds no vectors — an experiment that was
        never run, or one that has been deleted.
    """
    try:
        config = parse(identifier)
    except UnknownVariant:
        return None

    space = space_for(identifier)
    ids = await asyncio.to_thread(list_vector_ids, "", space)
    if not ids:
        return None

    grouped = _documents(ids)

    # One vector per document describes the whole of it, so a variant holding
    # three files costs three reads rather than one per chunk.
    describing = [
        vector_id_for(document_id, DESCRIBING_CHUNK) for document_id in grouped
    ]
    records = await asyncio.to_thread(fetch_vectors, describing, space)

    source_keys: list[str] = []
    expected = 0
    embedded_at = None
    embedding_model = ""
    interrupted = False

    for document_id, document_ids in grouped.items():
        metadata = (records.get(vector_id_for(document_id, DESCRIBING_CHUNK)) or {}).get(
            "metadata"
        ) or {}

        key = str(metadata.get(METADATA_SOURCE_KEY) or "")
        if key:
            source_keys.append(key)

        embedding_model = embedding_model or str(
            metadata.get(EMBEDDING_MODEL_METADATA_KEY) or ""
        )

        try:
            total = int(float(metadata.get(METADATA_CHUNK_TOTAL) or 0))
        except (TypeError, ValueError):
            total = 0
        expected += total

        # Fewer vectors than the last run said the file should have means the
        # run stopped partway. Scoring that variant would blame the strategy
        # for text that was never embedded, so it is reported, not hidden.
        if total and len(document_ids) < total:
            interrupted = True

        stamped = to_datetime(metadata.get(METADATA_EMBEDDED_AT))
        if stamped and (embedded_at is None or stamped > embedded_at):
            embedded_at = stamped

    return ChunkVariant(
        variant_id=identifier,
        label=label_for(config),
        config=config,
        embedding_model=embedding_model,
        source_keys=sorted(source_keys),
        vector_count=len(ids),
        chunk_total=expected,
        state=VariantState.INTERRUPTED if interrupted else VariantState.READY,
        embedded_at=embedded_at,
    )


async def list_variants() -> list[ChunkVariant]:
    """List every variant that currently holds vectors.

    Returns:
        The variants, newest embedding first. A namespace whose name is not a
        variant id is skipped rather than guessed at — something else wrote it,
        and inventing a configuration for it would put a row on screen that no
        strategy can reproduce.
    """
    namespaces = await asyncio.to_thread(
        namespace_stats, settings.pinecone_lab_index_name
    )

    described = await asyncio.gather(
        *(describe(name) for name in namespaces if name)
    )

    variants = [variant for variant in described if variant is not None]
    variants.sort(key=lambda variant: (variant.embedded_at is None, variant.embedded_at), reverse=True)

    logger.debug("%d variant(s) in %s", len(variants), settings.pinecone_lab_index_name)
    return variants


async def forget_source(source_key: str) -> int:
    """Remove one file's vectors from every variant that holds them.

    Called when the file itself is deleted. A variant holding chunks of a
    document that no longer exists is the `orphaned` state the pipeline warns
    about everywhere else, and it is worse here than in production: the chunks
    are still perfectly retrievable, so a comparison run would score four
    strategies on text nobody can look up any more.

    The variants themselves are left in place. An empty namespace ceases to
    exist on its own, and one still holding other files is still a valid
    experiment.

    Args:
        source_key: The object key that has been deleted.

    Returns:
        How many vectors were removed across every variant.
    """
    namespaces = await asyncio.to_thread(
        namespace_stats, settings.pinecone_lab_index_name
    )

    removed = 0
    for name in namespaces:
        if not name:
            continue
        try:
            space = space_for(name)
        except UnknownVariant:
            # Somebody else's namespace in the lab index. Not ours to empty.
            continue
        removed += await index_catalog.delete_document(source_key, space)

    if removed:
        logger.info("Removed %d variant vector(s) for %s", removed, source_key)

    return removed


async def delete(identifier: str) -> int:
    """Drop a variant and every vector in it.

    Args:
        identifier: The variant to remove.

    Returns:
        How many vectors were removed.

    Raises:
        UnknownVariant: If the id is not one this app can run — which also
            stops a stray id from being handed to a namespace-wide delete.
    """
    space = space_for(identifier)
    if not space.namespace:
        raise UnknownVariant("Production cannot be deleted as a variant.")

    ids = await asyncio.to_thread(list_vector_ids, "", space)
    await asyncio.to_thread(delete_namespace, space)

    logger.info("Dropped variant %s (%d vector(s))", identifier, len(ids))
    return len(ids)
