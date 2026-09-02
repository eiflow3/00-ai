"""Scoring a variant, and the two ways a scoreboard can lie.

The ranking is the output the whole feature exists to produce, so the tests here
are about the ways it could be wrong while still looking plausible:

  * **Counting an unanswerable question as a miss.** Four strategies each
    "failing" a question the document does not answer drags every score down by
    the same amount and makes a real difference harder to see — while implying
    a fault none of them has.
  * **Letting a failed row read as a wrong answer.** A row that errored was not
    measured; scoring it as incorrect would blame the strategy for a network
    blip.

The generated answer is faked from the retrieved chunks on purpose: it makes
correctness a function of retrieval, which is the relationship the scoreboard
claims to measure. A canned constant answer would make the `correct` column
true regardless of what came back, and the test would pass forever.
"""

import pytest

from app.schemas.golden import GoldenDifficulty, GoldenQuestionType, GoldenRow
from app.services import variant_scorer
from app.services.chunk_sections import section_spans

# A question the report answers, in a section it can be traced to.
ANSWERABLE = GoldenRow(
    row_id="r1",
    question_id="Q001",
    type=GoldenQuestionType.LOOKUP,
    difficulty=GoldenDifficulty.EASY,
    question="What was Cold Chain revenue in fiscal 2025?",
    answer="$734.1 million.",
    answer_keys=["734.1"],
    gold_sections=["SECTION 4. SEGMENT PERFORMANCE"],
)

# A question the report does not answer, which the golden set marks as such.
UNANSWERABLE = GoldenRow(
    row_id="r2",
    question_id="Q002",
    type=GoldenQuestionType.UNANSWERABLE,
    difficulty=GoldenDifficulty.HARD,
    question="What was revenue in the first quarter of fiscal 2026?",
    answer="The report does not state this.",
    must_refuse=True,
    gold_sections=[],
)


@pytest.fixture
def answers_from_context(monkeypatch):
    """Generate an answer by quoting the retrieved chunks.

    Faked at `get_adapter`, so `variant_scorer` still builds its prompt through
    the real prompt builder — only the provider call is replaced.
    """

    class _Adapter:
        def __init__(self) -> None:
            self.usage = None

        async def stream(self, messages, model, temperature=1.0):
            # The context block the prompt builder assembled is the last thing
            # the model would read, so quoting the whole prompt back is the
            # closest honest stand-in for a model that answers from context.
            yield " ".join(message["content"] for message in messages)

    monkeypatch.setattr(variant_scorer, "get_adapter", lambda provider: _Adapter())


async def test_a_variant_is_scored_on_what_it_retrieved(
    lab, report_key, report_text, index_variant, answers_from_context
):
    """The ordinary path: ask, retrieve, score, report."""
    await index_variant(report_key, "structural-512-64")

    result = await variant_scorer.score_variant(
        variant="structural-512-64",
        rows=[ANSWERABLE],
        text=report_text,
        spans=section_spans(report_text),
        top_k=5,
        generate=True,
        provider="openai",
        model="gpt-5.6-terra",
    )

    assert result.variant_id == "structural-512-64"
    assert result.label == "structural · 512/64"
    assert result.rows == 1
    assert result.failed == 0

    row = result.scores[0]
    assert row.retrieved_sections, "nothing was traced back to a section"
    assert row.recall is not None
    assert row.top_score > 0


async def test_an_unanswerable_row_is_left_out_of_the_recall_average(
    lab, report_key, report_text, index_variant, answers_from_context
):
    """It cites no section, so there is nothing to retrieve and nothing to miss.

    Counting it as a miss would put a ceiling on every variant's score and read
    as a shared failure rather than as a question with no answer.
    """
    await index_variant(report_key, "recursive-512-64")
    spans = section_spans(report_text)

    both = await variant_scorer.score_variant(
        variant="recursive-512-64",
        rows=[ANSWERABLE, UNANSWERABLE],
        text=report_text,
        spans=spans,
        generate=False,
    )
    just_answerable = await variant_scorer.score_variant(
        variant="recursive-512-64",
        rows=[ANSWERABLE],
        text=report_text,
        spans=spans,
        generate=False,
    )

    assert both.rows == 2
    # The unanswerable row is reported, and reported as unmeasured.
    assert both.scores[1].recall is None
    # And it does not move the average, which is what the grid renders as a
    # dash rather than a cross.
    assert both.recall == just_answerable.recall


async def test_retrieval_only_scoring_makes_no_claim_about_correctness(
    lab, report_key, report_text, index_variant
):
    """With no answer generated, `correct` is absent rather than false.

    False would render as "answered wrong" for every row in a run that never
    asked the model anything.
    """
    await index_variant(report_key, "boundary-512-64")

    result = await variant_scorer.score_variant(
        variant="boundary-512-64",
        rows=[ANSWERABLE, UNANSWERABLE],
        text=report_text,
        spans=section_spans(report_text),
        generate=False,
    )

    assert all(row.correct is None for row in result.scores)
    assert result.correct == 0
    assert all(row.answer == "" for row in result.scores)
    # Retrieval was still measured, which is the point of the cheap mode.
    assert result.recall > 0


async def test_a_row_that_fails_is_recorded_rather_than_counted_wrong(
    lab, report_key, report_text, index_variant, monkeypatch
):
    """One failed question must not throw away the nineteen that worked."""
    await index_variant(report_key, "fixed-512-64")

    async def explode(*args, **kwargs):
        raise RuntimeError("the embedding endpoint hung up")

    monkeypatch.setattr(variant_scorer, "retrieve", explode)

    result = await variant_scorer.score_variant(
        variant="fixed-512-64",
        rows=[ANSWERABLE],
        text=report_text,
        spans=section_spans(report_text),
        generate=False,
    )

    assert result.failed == 1
    assert result.scores[0].error
    assert result.scores[0].correct is None
    assert result.scores[0].question_id == "Q001"


async def test_two_variants_are_asked_the_same_questions(
    lab, report_key, report_text, index_variant
):
    """Held-still conditions are what make the comparison mean anything.

    Same rows, same `top_k`, same document — so a difference in recall is a
    difference in chunking and cannot be anything else.
    """
    await index_variant(report_key, "structural-512-64")
    await index_variant(report_key, "fixed-512-64")

    spans = section_spans(report_text)
    rows = [ANSWERABLE, UNANSWERABLE]

    scores = [
        await variant_scorer.score_variant(
            variant=variant,
            rows=rows,
            text=report_text,
            spans=spans,
            top_k=3,
            generate=False,
        )
        for variant in ("structural-512-64", "fixed-512-64")
    ]

    assert [score.rows for score in scores] == [2, 2]
    assert [
        [row.question_id for row in score.scores] for score in scores
    ] == [["Q001", "Q002"], ["Q001", "Q002"]]

    # And they were measured against the same gold sections, not their own.
    assert all(
        row.gold_sections == ANSWERABLE.gold_sections
        for score in scores
        for row in score.scores[:1]
    )


async def test_a_variant_holding_nothing_scores_zero_rather_than_erroring(
    lab, report_key, report_text
):
    """The state a person reaches by scoring before indexing.

    It has to be a legible zero: a variant that holds nothing retrieves
    nothing, which is a fact about the experiment, not a fault in the run.
    """
    result = await variant_scorer.score_variant(
        variant="recursive-512-64",
        rows=[ANSWERABLE],
        text=report_text,
        spans=section_spans(report_text),
        generate=False,
    )

    assert result.rows == 1
    assert result.failed == 0
    assert result.recall == 0.0
    assert result.scores[0].retrieved_sections == []
