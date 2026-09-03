"""Chunking strategies — what a client picks, previews and compares.

One document can be cut into embeddable segments several ways, and which way
you chose changes what retrieval can find.  These are the payloads that let a
client make that choice explicitly: the catalog of strategies on offer, a free
preview of how one of them cuts a given file, and the record of a *variant* —
a strategy plus its geometry — that has been embedded and can be queried.

The strategy enum is the contract.  Adding a strategy means an entry here, a
module under services/chunking, and a line in its registry; the registry
refuses to import if those three disagree, so a strategy can never be offered
through the API without an implementation behind it.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.services.chunking.tokens import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE
from app.services.embeddings import DEFAULT_EMBEDDING_MODEL


class ChunkStrategy(str, Enum):
    """How a document's text is cut into embeddable segments."""

    # A fixed token window, trimmed back to the last paragraph or sentence
    # break in its final quarter.  The pipeline's original behaviour, and the
    # baseline every other strategy is measured against.
    BOUNDARY = "boundary"

    # Fixed token windows, cut wherever the count runs out.  No boundary
    # search, so a chunk routinely opens and closes mid-sentence — the honest
    # floor a smarter strategy has to beat.
    FIXED = "fixed"

    # Separators tried in order — blank line, line, sentence, space — taking
    # the coarsest one that keeps the chunk within budget.  What most
    # production splitters do.
    RECURSIVE = "recursive"

    # The document's own headings decide the cuts.  Sections too long for the
    # budget are split inside themselves; sections too short to stand alone are
    # merged forward.
    STRUCTURAL = "structural"


# The strategy used when a caller names none — the pipeline's long-standing
# behaviour, so an untouched request chunks exactly as it always has.
DEFAULT_STRATEGY = ChunkStrategy.BOUNDARY


class ChunkingConfig(BaseModel):
    """A strategy and the geometry it runs at.

    Geometry belongs here rather than beside it because the two are not
    independent: `recursive` at 512/64 and `recursive` at 256/32 retrieve
    differently enough to be separate experiments, so they are separate
    variants and get separate vectors.
    """

    strategy: ChunkStrategy = Field(
        default=DEFAULT_STRATEGY, description="How the document's text is cut"
    )

    chunk_size: int = Field(
        default=DEFAULT_CHUNK_SIZE,
        ge=64,
        le=8000,
        description="Maximum tokens per chunk",
    )

    chunk_overlap: int = Field(
        default=DEFAULT_CHUNK_OVERLAP,
        ge=0,
        le=4000,
        description="Tokens repeated between consecutive chunks",
    )


class ChunkStrategySpec(BaseModel):
    """One strategy on offer, described for a person choosing between them."""

    id: ChunkStrategy = Field(..., description="Value to send back as `strategy`")

    label: str = Field(..., description="Short display name")

    # One line saying what this does to text, in behavioural terms. This is the
    # text a picker shows under the strategy's name.
    summary: str = Field(..., description="What this strategy does to a document")

    # When it tends to win, and when it does not. Longer than the summary and
    # shown on request rather than in the list.
    detail: str = Field(default="", description="Where this strategy helps or hurts")

    # Whether `chunk_overlap` changes anything for this strategy. A picker
    # disables the control rather than offering a setting that does nothing.
    honours_overlap: bool = Field(
        default=True, description="Whether overlap affects this strategy's output"
    )

    # Whether previewing or indexing with this strategy spends money on API
    # calls of its own, beyond the embeddings every strategy needs.
    costs_api_calls: bool = Field(
        default=False, description="Whether cutting itself calls a paid API"
    )


class PreviewChunk(BaseModel):
    """One chunk as a preview shows it, before anything is embedded."""

    chunk_index: int = Field(..., ge=0, description="Position in the document, 0-based")

    content: str = Field(..., description="The chunk's text")

    token_count: int = Field(..., ge=0, description="Tokens the embedding model will see")

    char_count: int = Field(..., ge=0, description="Characters in the chunk")

    start_offset: int = Field(..., ge=0, description="Character offset in the source text")

    end_offset: int = Field(..., ge=0, description="Where the chunk ends in the source text")

    # What the strategy has to say about this cut — the heading a structural
    # chunk sits under, or that a window was cut with no boundary in reach.
    # Diagnostic only; never embedded, never stored.
    note: str = Field(default="", description="Why this chunk ends where it does")


class ChunkPreviewStats(BaseModel):
    """The shape of a whole cut, which is what a preview is really for.

    Comparing two strategies by reading their chunks is slow; comparing them by
    these six numbers is immediate. A strategy producing four hundred-token
    chunks and one producing forty is a different experiment even at the same
    nominal size.
    """

    chunk_count: int = Field(default=0, ge=0, description="Chunks produced")

    total_tokens: int = Field(
        default=0, ge=0, description="Tokens across every chunk, overlap included"
    )

    document_tokens: int = Field(
        default=0, ge=0, description="Tokens in the document itself"
    )

    min_tokens: int = Field(default=0, ge=0, description="Smallest chunk")
    median_tokens: int = Field(default=0, ge=0, description="Middle chunk")
    max_tokens: int = Field(default=0, ge=0, description="Largest chunk")

    # How much of the embedded text is repetition. Overlap buys a sentence
    # spanning a boundary appearing whole on one side of it, and costs this
    # much extra embedding — worth seeing before paying for it.
    repeated_fraction: float = Field(
        default=0.0,
        ge=0.0,
        description="Share of embedded tokens that repeat a neighbouring chunk",
    )


class ChunkPreviewRequest(BaseModel):
    """Ask how a strategy would cut a file, without embedding anything."""

    source_key: str = Field(..., min_length=1, description="Object key to read")

    config: ChunkingConfig = Field(
        default_factory=ChunkingConfig, description="Strategy and geometry to apply"
    )


class ChunkPreviewResponse(BaseModel):
    """How one strategy cuts one file. Nothing is written or embedded."""

    source_key: str = Field(..., description="The file that was cut")

    # The variant this configuration would produce if it were indexed, so the
    # UI can say "this creates recursive · 512/64" before anything is spent.
    variant_id: str = Field(..., description="Variant this configuration would create")

    label: str = Field(..., description="Human-readable variant name")

    config: ChunkingConfig = Field(..., description="What was applied")

    stats: ChunkPreviewStats = Field(..., description="The shape of the cut")

    chunks: list[PreviewChunk] = Field(
        default_factory=list, description="Every chunk, in document order"
    )


class VariantState(str, Enum):
    """Whether a variant's vectors are a complete copy of what it should hold."""

    # Every chunk the last run produced is present.
    READY = "ready"

    # Fewer vectors than the run said the file should have: a run that stopped
    # partway. Reported rather than hidden, because scoring a half-embedded
    # variant would blame the strategy for missing text.
    INTERRUPTED = "interrupted"

    # Named, but holding nothing — a namespace emptied since it was chosen.
    # Only reachable through the production pointer, which is the one place a
    # variant is referred to by name after it has stopped existing.
    MISSING = "missing"


class ChunkVariant(BaseModel):
    """One strategy-and-geometry combination that has been embedded.

    Read back from the vector index rather than from a job table, so the list
    survives a restart and cannot claim a variant that no longer exists.
    """

    variant_id: str = Field(..., description="Id to query against, stable for the config")

    label: str = Field(..., description="Human-readable name, e.g. 'recursive · 512/64'")

    config: ChunkingConfig = Field(..., description="How this variant was cut")

    embedding_model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL, description="Model its vectors came from"
    )

    source_keys: list[str] = Field(
        default_factory=list, description="Files embedded under this variant"
    )

    vector_count: int = Field(default=0, ge=0, description="Vectors it holds")

    chunk_total: int = Field(
        default=0, ge=0, description="Vectors the last run said it should hold"
    )

    state: VariantState = Field(
        default=VariantState.READY, description="Whether it is complete"
    )

    embedded_at: Optional[datetime] = Field(
        default=None, description="When it was last written"
    )


class VariantDeleteResponse(BaseModel):
    """What deleting a variant removed."""

    variant_id: str = Field(..., description="The variant that was dropped")

    deleted: int = Field(default=0, ge=0, description="Vectors removed")


class ProductionSpace(BaseModel):
    """Where the application answers questions from.

    Production is a pointer, not a place: one stored variant id naming the
    namespace `/chat` reads when a request does not name one itself.  Moving it
    is how a comparison turns into a decision — the winner of a scoreboard run
    becomes the default answer with nothing re-embedded and nothing copied.

    An empty `variant_id` is the original production index, which is what an
    installation that has never run an experiment answers from.
    """

    variant_id: str = Field(
        default="",
        description="Variant answering by default. Empty is the original index.",
    )

    label: str = Field(..., description="How it should read on screen")

    state: VariantState = Field(
        default=VariantState.READY,
        description="Whether the space it names can actually answer",
    )

    vector_count: int = Field(default=0, ge=0, description="Vectors it holds")

    source_keys: list[str] = Field(
        default_factory=list, description="Files it can answer about"
    )

    updated_at: Optional[datetime] = Field(
        default=None, description="When production was last pointed somewhere"
    )

    # Whether there is anything to go back to. The original index can be
    # retired once production points at a variant, and a client that offered
    # "back to the original index" regardless would be offering an action that
    # can only fail.
    original_vector_count: int = Field(
        default=0, ge=0, description="Vectors the original production index still holds"
    )


class ProductionSpaceRequest(BaseModel):
    """Ask that production answer from a different space."""

    variant_id: str = Field(
        default="",
        description=(
            "Variant to answer from, e.g. 'recursive-512-64'. Empty points "
            "back at the original index."
        ),
    )
