"""The vocabulary an evaluation is written in.

Served to clients rather than hardcoded in them, for the same reason the model
list is: if each client invents its own reason codes, the evaluations pile up as
free text and nothing can be counted.

The tags are grouped by the stage they can explain, which is the point of the
whole feature — "the answer was wrong" is not actionable, "the right chunk was
never retrieved" is.  A reason that could belong to either stage is a sign the
tag is badly worded, not that it needs to appear twice.
"""

from app.schemas.evaluation import (
    EvaluationOptions,
    EvaluationTarget,
    TagOption,
    Verdict,
    VerdictOption,
)

# The verdicts, worst last so a client renders them in a consistent order.
VERDICTS: tuple[VerdictOption, ...] = (
    VerdictOption(
        id=Verdict.GOOD,
        label="Good",
        hint="Did its job — nothing to fix here.",
    ),
    VerdictOption(
        id=Verdict.PARTIAL,
        label="Partial",
        hint="Half right, or right but incomplete.",
    ),
    VerdictOption(
        id=Verdict.BAD,
        label="Bad",
        hint="Wrong enough that the answer could not recover.",
    ),
)

# Reason chips. Each belongs to exactly one stage — that scoping is what makes
# a bad answer resolvable into a retrieval problem or a generation problem.
TAGS: tuple[TagOption, ...] = (
    # --- Retrieval: was the right evidence found, and ranked usefully? ------
    TagOption(
        id="nothing_retrieved",
        label="Nothing retrieved",
        target=EvaluationTarget.RETRIEVAL,
        hint="The search came back empty, so the answer was ungrounded.",
    ),
    TagOption(
        id="missing_chunk",
        label="Missing the answer",
        target=EvaluationTarget.RETRIEVAL,
        hint="The passage that answers this exists in the corpus but was not returned.",
    ),
    TagOption(
        id="wrong_chunks",
        label="Irrelevant chunks",
        target=EvaluationTarget.RETRIEVAL,
        hint="What came back was about something else.",
    ),
    TagOption(
        id="ranked_low",
        label="Right chunk ranked low",
        target=EvaluationTarget.RETRIEVAL,
        hint="The answer was in the results, but buried under weaker matches.",
    ),
    TagOption(
        id="below_threshold",
        label="Right chunk dropped",
        target=EvaluationTarget.RETRIEVAL,
        hint="The score threshold cut the passage that would have answered it.",
    ),
    TagOption(
        id="wrong_source",
        label="Wrong document",
        target=EvaluationTarget.RETRIEVAL,
        hint="Chunks came from a file that should not have matched at all.",
    ),
    TagOption(
        id="redundant_chunks",
        label="Duplicated chunks",
        target=EvaluationTarget.RETRIEVAL,
        hint="Several results said the same thing, crowding out the rest.",
    ),
    # --- Generation: was the answer faithful to what it was given? ---------
    TagOption(
        id="ignored_context",
        label="Ignored the context",
        target=EvaluationTarget.GENERATION,
        hint="The answer was in the retrieved chunks and the model did not use it.",
    ),
    TagOption(
        id="hallucinated",
        label="Made something up",
        target=EvaluationTarget.GENERATION,
        hint="Asserted a fact that appears nowhere in the retrieved chunks.",
    ),
    TagOption(
        id="bad_arithmetic",
        label="Arithmetic wrong",
        target=EvaluationTarget.GENERATION,
        hint="The figures were there; the calculation on them was not.",
    ),
    TagOption(
        id="wrong_period",
        label="Wrong year or period",
        target=EvaluationTarget.GENERATION,
        hint="Quoted the comparative instead of the period that was asked about.",
    ),
    TagOption(
        id="refused_answerable",
        label="Refused an answerable question",
        target=EvaluationTarget.GENERATION,
        hint="Declined even though the retrieved chunks contained the answer.",
    ),
    TagOption(
        id="answered_unanswerable",
        label="Answered an unanswerable question",
        target=EvaluationTarget.GENERATION,
        hint="Should have said the corpus does not state this, and did not.",
    ),
    TagOption(
        id="incomplete",
        label="Left part unanswered",
        target=EvaluationTarget.GENERATION,
        hint="Answered one half of a two-part question.",
    ),
    TagOption(
        id="no_citation",
        label="Did not attribute",
        target=EvaluationTarget.GENERATION,
        hint="Right answer, but nothing tying it to a source.",
    ),
    TagOption(
        id="verbose",
        label="Padded",
        target=EvaluationTarget.GENERATION,
        hint="Correct, but buried in restatement.",
    ),
    # --- Overall: things neither stage owns ---------------------------------
    TagOption(
        id="ambiguous_question",
        label="Question was ambiguous",
        target=EvaluationTarget.OVERALL,
        hint="The pipeline behaved reasonably; the question had more than one reading.",
    ),
    TagOption(
        id="not_in_corpus",
        label="Not in the corpus",
        target=EvaluationTarget.OVERALL,
        hint="Nothing indexed could have answered this — an ingestion gap, not a bug.",
    ),
    TagOption(
        id="config_fault",
        label="Settings were wrong",
        target=EvaluationTarget.OVERALL,
        hint="Wrong top-k, threshold or embedding model for this question.",
    ),
)

# Indexed once at import; the catalog is static.
_TAGS_BY_ID = {tag.id: tag for tag in TAGS}


def options() -> EvaluationOptions:
    """Return every choice a client may offer when evaluating an answer."""
    return EvaluationOptions(
        verdicts=list(VERDICTS),
        tags=list(TAGS),
        targets=list(EvaluationTarget),
    )


def tags_for(target: EvaluationTarget) -> list[TagOption]:
    """Return the reason chips that can explain one stage's verdict."""
    return [tag for tag in TAGS if tag.target == target]


def validate_tags(target: EvaluationTarget, tags: list[str]) -> list[str]:
    """Check that every tag exists and belongs to the stage being judged.

    Args:
        target: The stage the judgement is about.
        tags: Tag ids supplied by the client.

    Returns:
        The tags, de-duplicated and in catalog order so stored rows are
        comparable regardless of the order they were clicked in.

    Raises:
        ValueError: If a tag is unknown, or belongs to a different stage —
            a retrieval reason on a generation verdict is a client bug, and
            silently dropping it would hide the very fault being recorded.
    """
    unknown = [tag for tag in tags if tag not in _TAGS_BY_ID]
    if unknown:
        raise ValueError(
            f"Unknown evaluation tag(s): {', '.join(sorted(unknown))}. "
            f"Use the ids from GET /evaluations/options."
        )

    misplaced = [tag for tag in tags if _TAGS_BY_ID[tag].target != target]
    if misplaced:
        raise ValueError(
            f"Tag(s) {', '.join(sorted(misplaced))} do not belong to target "
            f"'{target.value}'. Judge that stage separately instead."
        )

    chosen = set(tags)
    return [tag.id for tag in TAGS if tag.id in chosen]
