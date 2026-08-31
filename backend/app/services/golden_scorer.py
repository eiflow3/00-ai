"""Deterministic scoring of one answer against one golden row.

This is the arbiter of whether a RAG answer is right, and it lived in
`evals/run_eval.py` until the golden set generator needed it too.  Two copies
would have drifted, and the drift would have been silent: the offline harness
would keep reporting a score the in-app self-check no longer agreed with, and
nothing would say which one was lying.  So the logic moved here and the CLI
imports it.

Deliberately stdlib-only and framework-free — no pydantic, no FastAPI, not even
a schema import.  That is what lets `evals/run_eval.py` run against a bare
Python with none of the backend's dependencies installed, which is the whole
point of an offline harness.

Three rules, in the order they are applied:

  * **forbidden** — a distractor the answer fell for fails the row outright,
    whatever else the answer got right.
  * **unanswerable** — the answer must decline, *and* still state whatever half
    of the question the document does answer.
  * **answerable** — a numeric target is authoritative when the row defines
    one; otherwise every answer key must appear.

There is no LLM judge, on purpose.  A score is only useful if a run from six
months ago is comparable to one from today, and a judge that is itself a model
makes that untrue the moment the judge changes.
"""

import re
from typing import Any, Optional

# Applied when a golden row gives a numeric answer without saying how close is
# close enough.  Tight, because most rows quote a figure rather than derive it.
DEFAULT_NUMERIC_TOLERANCE = 0.05

# How many of the answer's own numbers to quote back when a numeric row fails.
# Enough to see what the model latched onto, short enough to read.
FAILURE_NUMBER_SAMPLE = 8

# The ways an answer can admit the document does not say.  A prompt that makes
# the model decline in some other phrasing belongs here — loosening a golden
# row instead would weaken the question for every future run.
REFUSAL_PATTERNS: tuple[str, ...] = (
    r"\bnot stated\b",
    r"\bnot (?:be )?(?:specified|disclosed|provided|reported|included|given)\b",
    r"\bdoes not (?:state|say|specify|disclose|provide|report|contain|include|break)\b",
    r"\bdoesn't (?:state|say|specify|disclose|provide|report|contain|include|break)\b",
    r"\bno (?:information|breakdown|figure|data|mention|disclosure)\b",
    r"\bcannot (?:be )?(?:determined|answered|found)\b",
    r"\bcan't be (?:determined|answered|found)\b",
    r"\bnot available\b",
    r"\bis not in the (?:report|document|context)\b",
    r"\bunable to (?:answer|determine|find)\b",
    r"\bhad not closed\b",
    r"\bnot yet closed\b",
    r"\bonly provides guidance\b",
    r"\bguidance,? not actual\b",
)

_WHITESPACE = re.compile(r"\s+")

# Tolerates thousands separators so "2,833.0" reads as one number rather than
# two.  Currency and percent signs are left outside the match, which is why a
# bare figure and "$2,833.0 million" score the same.
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

_REFUSALS = tuple(re.compile(pattern) for pattern in REFUSAL_PATTERNS)


def norm(text: str) -> str:
    """Collapse whitespace and lowercase, so matching ignores formatting.

    Args:
        text: Any string, or None.

    Returns:
        The comparable form of the text.
    """
    return _WHITESPACE.sub(" ", (text or "")).strip().lower()


def number_tokens(text: str) -> list[str]:
    """Pull every numeric token out of a string, exactly as it was written.

    The verbatim form matters to the golden set generator: a row's answer key
    must be the string the document uses, "2,833.0" and not "2833.0", or it
    will never match when the harness looks for it.

    Args:
        text: The text to scan.

    Returns:
        Each numeric token, in the order it appeared.
    """
    return _NUMBER.findall(text or "")


def numbers_in(text: str) -> list[float]:
    """Pull every numeric token out of a string, as numbers.

    Args:
        text: The text to scan.

    Returns:
        Each number found, in the order it appeared, commas stripped.
    """
    found: list[float] = []
    for token in number_tokens(text):
        try:
            found.append(float(token.replace(",", "")))
        except ValueError:
            pass
    return found


def looks_like_refusal(answer: str) -> bool:
    """Whether an answer admits the document does not state the fact.

    Args:
        answer: The model's answer.

    Returns:
        True when any refusal pattern matches.
    """
    low = norm(answer)
    return any(pattern.search(low) for pattern in _REFUSALS)


def score_row(gold: dict[str, Any], pred: dict[str, Any]) -> dict[str, Any]:
    """Score one prediction against the golden row it answers.

    Args:
        gold: A golden row — `id`, `type`, `difficulty`, and whichever of
            `answer_keys`, `forbidden_keys`, `numeric_answer`,
            `numeric_tolerance`, `must_refuse` and `gold_sections` it carries.
        pred: The prediction — `answer`, and optionally `retrieved_sections`.

    Returns:
        The verdict: `correct`, the `reasons` it was not, the `support`
        fraction of answer keys present, and `recall`/`precision` over the
        gold sections when the prediction reported what it retrieved.
    """
    answer = pred.get("answer", "") or ""
    low = norm(answer)
    reasons: list[str] = []

    for forbidden in gold.get("forbidden_keys", []):
        if norm(forbidden) in low:
            reasons.append(f"contains forbidden key '{forbidden}'")

    if gold.get("must_refuse"):
        if not looks_like_refusal(answer):
            reasons.append("did not acknowledge the report does not state this")
        # A partially answerable row still needs its stated half present.
        for key in gold.get("answer_keys", []):
            if norm(key) not in low:
                reasons.append(f"missing stated fact '{key}'")
        correct = not reasons
    elif gold.get("numeric_answer") is not None:
        target = float(gold["numeric_answer"])
        tolerance = float(gold.get("numeric_tolerance", DEFAULT_NUMERIC_TOLERANCE))
        found = numbers_in(answer)
        hit = any(abs(number - target) <= tolerance for number in found)
        if not hit:
            sample = found[:FAILURE_NUMBER_SAMPLE] or "no numbers"
            reasons.append(f"expected {target} (+/-{tolerance}), answer had {sample}")
        correct = hit and not reasons
    else:
        missing = [k for k in gold.get("answer_keys", []) if norm(k) not in low]
        if missing:
            reasons.append(f"missing keys: {missing}")
        correct = not missing and not reasons

    # Support signal: did the answer cite the underlying figures at all?  Kept
    # apart from correctness because a numeric row can be right without
    # quoting a key, and that is worth seeing rather than punishing.
    keys = gold.get("answer_keys", [])
    support = (sum(norm(k) in low for k in keys) / len(keys)) if keys else None

    recall, precision = _retrieval_metrics(
        gold.get("gold_sections", []), pred.get("retrieved_sections")
    )

    return {
        "id": gold["id"],
        "type": gold["type"],
        "difficulty": gold["difficulty"],
        "correct": bool(correct),
        "support": support,
        "recall": recall,
        "precision": precision,
        "reasons": reasons,
        "answer": answer,
    }


def _retrieval_metrics(
    gold_sections: list[str], retrieved: Optional[list[str]]
) -> tuple[Optional[float], Optional[float]]:
    """Compare the sections retrieved against the ones the answer needed.

    This is what separates "retrieved the wrong chunk" from "retrieved the
    right chunk and answered badly", which are different bugs with different
    fixes.

    Args:
        gold_sections: Section headings the answer should have come from.
        retrieved: Headings the prediction says it retrieved, or None when the
            prediction did not report them.

    Returns:
        Recall and precision, or `(None, None)` when either side is empty.
    """
    if retrieved is None or not gold_sections:
        return None, None

    found = {norm(section) for section in retrieved}
    wanted = {norm(section) for section in gold_sections}
    hits = len(wanted & found)
    return hits / len(wanted), (hits / len(found) if found else 0.0)
