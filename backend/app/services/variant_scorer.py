"""Scores one chunking variant against a golden set.

The measurement the whole comparison rests on.  Every variant is asked the same
questions, with the same model, the same prompt and the same `top_k`, so the
only thing that differs between two results is where the chunks came from.

The scoring itself is not this module's: `services.golden_scorer` already
decides whether an answer is right and how much of the gold section was
retrieved, and it is the same function the offline harness in `evals/` uses.
Having a second one here would mean the app and the harness could quietly
disagree about the same answer, and nothing would say which was lying.

What this module adds is the middle: run retrieval in one variant's vector
space, work out which sections the chunks came from, optionally generate an
answer, and hand both to the scorer.
"""

import logging
import time
from typing import Awaitable, Callable, Optional

from app.schemas.golden import GoldenRow
from app.schemas.prompt import PromptId
from app.schemas.variant_score import RowScore, VariantScore
from app.services import chunk_variants, golden_export, golden_scorer
from app.services.chunk_sections import sections_for
from app.services.llm.factory import get_adapter
from app.services.prompt_builder import build_messages, resolve_system_prompt
from app.services.retrieval import retrieve

logger = logging.getLogger(__name__)

# Reported when a row could not be scored at all, so a failed row is visibly
# different from one that was scored and got it wrong.
ROW_FAILED = "could not be scored"

# Called after each row, so a run can report progress while it works.
RowReporter = Callable[[RowScore], Awaitable[None]]


async def _answer(
    question: str,
    chunks: list,
    provider: str,
    model: str,
    prompts: dict[PromptId, str],
) -> str:
    """Generate one answer, through the same path a chat request takes.

    Deliberately the same prompt builder and the same adapter the live endpoint
    uses. A score produced under different wording would measure that wording
    rather than the chunking.

    Args:
        question: The golden row's question.
        chunks: What retrieval returned for it.
        provider: LLM provider key.
        model: Model id.
        prompts: The prompt templates in force.

    Returns:
        The answer text, assembled from the stream.
    """
    adapter = get_adapter(provider)
    messages = build_messages(
        query=question,
        chunks=chunks,
        system_prompt=resolve_system_prompt(None, prompts),
        grounded=True,
        prompts=prompts,
    )

    parts: list[str] = []
    async for delta in adapter.stream(messages, model):
        parts.append(delta)
    return "".join(parts)


async def _score_row(
    row: GoldenRow,
    variant: str,
    text: str,
    spans: list[tuple[str, int, int]],
    top_k: int,
    generate: bool,
    provider: str,
    model: str,
    prompts: dict[PromptId, str],
) -> RowScore:
    """Put one question to one variant and score what comes back."""
    gold = golden_export.build_line(row)

    result = await retrieve(row.question, top_k=top_k, variant=variant)

    # Which sections the retrieved text actually came from. Computed from the
    # document rather than read off the chunk, because only one of the four
    # strategies knows what a section is.
    retrieved: list[str] = []
    for chunk in result.chunks:
        for title in sections_for(chunk.content, text, spans):
            if title not in retrieved:
                retrieved.append(title)

    answer = ""
    if generate:
        answer = await _answer(row.question, result.chunks, provider, model, prompts)

    verdict = golden_scorer.score_row(
        gold, {"answer": answer, "retrieved_sections": retrieved}
    )

    return RowScore(
        question_id=row.question_id or row.row_id,
        question=row.question,
        # Only meaningful when an answer was generated: scoring an empty string
        # would mark every row wrong and call it a chunking failure.
        correct=bool(verdict["correct"]) if generate else None,
        recall=verdict["recall"],
        precision=verdict["precision"],
        top_score=max((chunk.score for chunk in result.chunks), default=0.0),
        gold_sections=row.gold_sections,
        retrieved_sections=retrieved,
        answer=answer,
        reasons=list(verdict["reasons"]) if generate else [],
    )


def _totals(variant: str, scores: list[RowScore], elapsed: float) -> VariantScore:
    """Fold a variant's per-row results into the numbers that get compared."""
    measured = [score.recall for score in scores if score.recall is not None]
    precisions = [score.precision for score in scores if score.precision is not None]

    try:
        config = chunk_variants.parse(variant) if variant else None
        label = chunk_variants.label_for(config) if config else "production index"
    except chunk_variants.UnknownVariant:
        config, label = None, variant

    return VariantScore(
        variant_id=variant,
        label=label,
        config=config,
        rows=len(scores),
        correct=sum(1 for score in scores if score.correct),
        # Averaged over the rows that had something to compare. An unanswerable
        # row cites no section, and counting it as a miss would punish every
        # variant for a question nothing could retrieve.
        recall=sum(measured) / len(measured) if measured else 0.0,
        precision=sum(precisions) / len(precisions) if precisions else 0.0,
        failed=sum(1 for score in scores if score.error),
        duration_seconds=elapsed,
        scores=scores,
    )


async def score_variant(
    variant: str,
    rows: list[GoldenRow],
    text: str,
    spans: list[tuple[str, int, int]],
    top_k: int = 5,
    generate: bool = True,
    provider: str = "",
    model: str = "",
    prompts: Optional[dict[PromptId, str]] = None,
    on_row: Optional[RowReporter] = None,
) -> VariantScore:
    """Put a whole golden set to one variant.

    Args:
        variant: The variant to search. Empty is the production index.
        rows: The golden rows to ask, in set order.
        text: The source document, for mapping chunks back to sections.
        spans: Section spans from `chunk_sections.section_spans`, computed once
            for the document and shared across every variant.
        top_k: Chunks each question retrieves.
        generate: Whether to answer each question or only retrieve for it.
        provider: LLM provider, when generating.
        model: Model id, when generating.
        prompts: The prompt templates in force.
        on_row: Called after each row, so a run can report as it goes.

    Returns:
        The variant's totals, with every row's result.
    """
    started = time.monotonic()
    scores: list[RowScore] = []

    for row in rows:
        try:
            score = await _score_row(
                row, variant, text, spans, top_k, generate, provider, model, prompts or {}
            )
        except Exception as exc:
            # One question failing is a row that could not be scored, not a
            # variant that lost. Recorded and carried past, so nineteen answers
            # are not thrown away to punish the twentieth.
            logger.warning("%s: %s failed: %s", variant or "production", row.question_id, exc)
            score = RowScore(
                question_id=row.question_id or row.row_id,
                question=row.question,
                gold_sections=row.gold_sections,
                error=f"{ROW_FAILED}: {exc}",
            )

        scores.append(score)
        if on_row is not None:
            await on_row(score)

    totals = _totals(variant, scores, time.monotonic() - started)

    logger.info(
        "%s scored: %d/%d correct, recall %.2f over %d row(s)",
        variant or "production",
        totals.correct,
        totals.rows,
        totals.recall,
        totals.rows,
    )

    return totals
