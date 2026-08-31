"""Asks a model for evaluation questions, and turns the reply into rows.

Three passes, because the three kinds of question need three different views of
the document and mixing them produces worse questions than asking separately.

  * **Per section** — the section verbatim, so answer keys can be copied rather
    than remembered. This is where lookup and distractor rows come from.
  * **Cross section** — the fact digest and the outline, never the prose. A
    model handed the whole document writes questions it can answer by quoting;
    handed only figures, it has to join or compute.
  * **Unanswerable** — the outline and the digest, asked for what is missing.

Nothing here decides whether a question is *correct*.  That is
`golden_validator`'s job, deliberately kept apart: this module is the only one
that trusts the model, and it is the only one that can be wrong on its own.
What it does guarantee is shape — a row that parses, carries the fields the
harness needs, and is typed as one of the kinds the pass was allowed to
produce. Anything malformed is retried once and then reported, because a
failed section should cost that section rather than the run.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Optional

from app.schemas.golden import (
    DocumentSection,
    GoldenDerivation,
    GoldenDifficulty,
    GoldenQuestionType,
    GoldenRow,
)
from app.schemas.prompt import PromptId
from app.services import golden_catalog, prompt_catalog
from app.services.golden_facts import FactDigest
from app.services.llm.base import BaseLLMAdapter

logger = logging.getLogger(__name__)

# One retry when the model returns something that is not JSON. A second retry
# has never been the difference in practice, and each one costs a call.
PARSE_ATTEMPTS = 2

# Model calls in flight at once. Sections are independent, so drafting them
# concurrently turns a dozen sequential calls into about two rounds — but not
# unbounded, or a long document opens fifty connections and hits a rate limit.
MAX_CONCURRENT_SECTIONS = 4

# Fenced code blocks a model wraps JSON in, despite being asked not to.
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

# Fallback when the reply has prose around the JSON: the outermost braces.
_BRACES = re.compile(r"\{.*\}", re.DOTALL)


class DraftFailed(RuntimeError):
    """Raised when a pass could not be turned into rows.

    Carries which pass failed so the run can report it and carry on with the
    others, rather than losing every section because one came back malformed.
    """

    def __init__(self, detail: str, message: str) -> None:
        self.detail = detail
        self.message = message
        super().__init__(f"{detail}: {message}")


async def draft_section(
    adapter: BaseLLMAdapter,
    model: str,
    template: str,
    section: DocumentSection,
    digest: FactDigest,
    outline: list[str],
    count: int,
) -> list[GoldenRow]:
    """Draft the questions answerable from one section.

    Args:
        adapter: Provider adapter to call.
        model: Model id.
        template: The section prompt, as currently in force.
        section: The section to draft from.
        digest: The document's facts, for the section's figure list.
        outline: Titles a row may cite.
        count: How many questions to ask for.

    Returns:
        The drafted rows, defaulting their gold_sections to this section.

    Raises:
        DraftFailed: When the reply could not be parsed.
    """
    prompt = prompt_catalog.render(
        template,
        {
            "section_title": section.title,
            "section_text": section.body,
            "section_facts": digest.render(section.title) or "(none)",
            "outline": "\n".join(outline),
            "count": str(count),
            "types": ", ".join(t.value for t in golden_catalog.SECTION_TYPES),
        },
    )
    payload = await _ask(adapter, model, prompt, detail=section.title)
    return _to_rows(payload, allowed=golden_catalog.SECTION_TYPES, default_section=section.title)


async def draft_cross_section(
    adapter: BaseLLMAdapter,
    model: str,
    template: str,
    digest: FactDigest,
    outline: list[str],
    count: int,
    allow_arithmetic: bool,
) -> list[GoldenRow]:
    """Draft the questions that need two sections at once, or a computation.

    Args:
        adapter: Provider adapter to call.
        model: Model id.
        template: The cross-section prompt, as currently in force.
        digest: The document's facts.
        outline: Titles a row may cite.
        count: How many questions to ask for.
        allow_arithmetic: False when the document states too few figures.

    Returns:
        The drafted rows.

    Raises:
        DraftFailed: When the reply could not be parsed.
    """
    types = golden_catalog.cross_section_types(allow_arithmetic)
    prompt = prompt_catalog.render(
        template,
        {
            "outline": "\n".join(outline),
            "facts": digest.render() or "(none)",
            "count": str(count),
            "types": ", ".join(types),
        },
    )
    payload = await _ask(adapter, model, prompt, detail="cross-section")
    allowed = tuple(GoldenQuestionType(t) for t in types)
    return _to_rows(payload, allowed=allowed)


async def draft_unanswerable(
    adapter: BaseLLMAdapter,
    model: str,
    template: str,
    digest: FactDigest,
    outline: list[str],
    count: int,
) -> list[GoldenRow]:
    """Draft the questions the document does not answer.

    Args:
        adapter: Provider adapter to call.
        model: Model id.
        template: The unanswerable prompt, as currently in force.
        digest: The document's facts.
        outline: Titles a row may cite.
        count: How many questions to ask for.

    Returns:
        The drafted rows, every one flagged must_refuse.

    Raises:
        DraftFailed: When the reply could not be parsed.
    """
    prompt = prompt_catalog.render(
        template,
        {
            "outline": "\n".join(outline),
            "facts": digest.render() or "(none)",
            "count": str(count),
        },
    )
    payload = await _ask(adapter, model, prompt, detail="unanswerable")
    rows = _to_rows(payload, allowed=golden_catalog.UNANSWERABLE_TYPES)

    # The pass exists to produce refusals, so the flag is set here rather than
    # trusted from the reply. A row typed unanswerable without it would be
    # scored as if a figure were expected, which is the opposite of the test.
    for row in rows:
        row.type = GoldenQuestionType.UNANSWERABLE
        row.must_refuse = True
    return rows


def templates_for(active: dict[PromptId, str]) -> dict[PromptId, str]:
    """Pull the three generator templates out of the prompts in force.

    Args:
        active: Every prompt in force, from `prompt_store.active`.

    Returns:
        Just the generator's, so a caller cannot accidentally pass a chat
        prompt to a drafting pass.
    """
    return {
        prompt_id: active[prompt_id]
        for prompt_id in (
            PromptId.GOLDEN_SECTION,
            PromptId.GOLDEN_CROSS_SECTION,
            PromptId.GOLDEN_UNANSWERABLE,
        )
    }


async def _ask(
    adapter: BaseLLMAdapter, model: str, prompt: str, detail: str
) -> dict[str, Any]:
    """Send one prompt and parse the JSON it returns.

    The adapters stream, because chat needs them to. Here the whole reply is
    wanted at once, so the deltas are accumulated and parsed after.

    Args:
        adapter: Provider adapter to call.
        model: Model id.
        prompt: The rendered prompt.
        detail: What is being drafted, for the error message.

    Returns:
        The parsed reply.

    Raises:
        DraftFailed: When no attempt produced parseable JSON.
    """
    messages = [{"role": "user", "content": prompt}]
    last_error = ""

    for attempt in range(PARSE_ATTEMPTS):
        try:
            reply = "".join([chunk async for chunk in adapter.stream(messages, model)])
        except Exception as exc:  # provider errors vary by SDK
            raise DraftFailed(detail, f"the model call failed: {exc}") from exc

        try:
            return _parse(reply)
        except ValueError as exc:
            last_error = str(exc)
            logger.warning("Golden draft for %s did not parse (attempt %d): %s",
                           detail, attempt + 1, last_error)
            # Ask again, showing what came back, rather than reformatting it
            # here — a reply we have to repair by hand is one we cannot trust.
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": reply[:4000]},
                {
                    "role": "user",
                    "content": (
                        "That was not valid JSON. Reply again with the JSON object only "
                        "— no prose, no code fence."
                    ),
                },
            ]

    raise DraftFailed(detail, f"the reply was not JSON after {PARSE_ATTEMPTS} attempts: {last_error}")


def _parse(reply: str) -> dict[str, Any]:
    """Turn a model reply into an object, tolerating a code fence around it.

    Args:
        reply: Everything the model said.

    Returns:
        The parsed object.

    Raises:
        ValueError: When nothing in the reply parses as a JSON object.
    """
    cleaned = _FENCE.sub("", reply or "").strip()
    if not cleaned:
        raise ValueError("the reply was empty")

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = _BRACES.search(cleaned)
        if not match:
            raise ValueError("no JSON object in the reply")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSON: {exc}")

    if not isinstance(parsed, dict):
        raise ValueError("the reply was not a JSON object")
    return parsed


def _to_rows(
    payload: dict[str, Any],
    allowed: tuple[GoldenQuestionType, ...],
    default_section: str = "",
) -> list[GoldenRow]:
    """Build rows from a parsed reply, dropping anything unusable.

    A row missing a question or an answer is not a row, and there is nothing to
    flag it as. Everything else is kept and left to the validator, which can
    say precisely what is wrong with it.

    Args:
        payload: The parsed reply.
        allowed: Types this pass was permitted to produce.
        default_section: Section to cite when the model named none.

    Returns:
        The rows that had enough to build.
    """
    rows: list[GoldenRow] = []

    for entry in payload.get("rows", []):
        if not isinstance(entry, dict):
            continue
        question = str(entry.get("question", "")).strip()
        answer = str(entry.get("answer", "")).strip()
        if not question or not answer:
            continue

        sections = _strings(entry.get("gold_sections"))
        if not sections and default_section and not entry.get("must_refuse"):
            sections = [default_section]

        rows.append(
            GoldenRow(
                row_id=str(uuid.uuid4()),
                type=_type(entry.get("type"), allowed),
                difficulty=_difficulty(entry.get("difficulty")),
                question=question,
                answer=answer,
                numeric_answer=_number(entry.get("numeric_answer")),
                numeric_tolerance=_number(entry.get("numeric_tolerance")),
                answer_keys=_strings(entry.get("answer_keys")),
                forbidden_keys=_strings(entry.get("forbidden_keys")),
                must_refuse=bool(entry.get("must_refuse", False)),
                gold_sections=sections,
                note=str(entry.get("note", "") or ""),
                derivation=_derivation(entry.get("derivation")),
            )
        )

    return rows


def _type(value: Any, allowed: tuple[GoldenQuestionType, ...]) -> GoldenQuestionType:
    """Coerce a reported type, falling back to the pass's first allowed one."""
    try:
        parsed = GoldenQuestionType(str(value))
    except ValueError:
        return allowed[0]
    return parsed if parsed in allowed else allowed[0]


def _difficulty(value: Any) -> GoldenDifficulty:
    """Coerce a reported difficulty, defaulting to medium."""
    try:
        return GoldenDifficulty(str(value))
    except ValueError:
        return GoldenDifficulty.MEDIUM


def _number(value: Any) -> Optional[float]:
    """Coerce a reported figure, or None when it is absent or not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strings(value: Any) -> list[str]:
    """Coerce a reported list of strings, dropping blanks."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _derivation(value: Any) -> Optional[GoldenDerivation]:
    """Coerce reported working, or None when there is none to check."""
    if not isinstance(value, dict):
        return None

    operands = [n for n in (_number(v) for v in value.get("operands", [])) if n is not None]
    if not operands:
        return None

    return GoldenDerivation(
        operands=operands,
        operator=str(value.get("operator", "")).strip(),
        explanation=str(value.get("explanation", "") or ""),
    )
