"""The prompts the pipeline ships with, and the variables each may interpolate.

This is the registry the Generation phase is built from.  It holds the *default*
text only — what is actually in force comes from `prompt_store`, which layers a
saved override on top.  Keeping the two apart means a reset always has something
to return to, and a bad edit is never unrecoverable.

Every default here is the wording the pipeline used when these lived as module
constants, except where a prompt is new.  Two are: a system prompt (there was
never one, so the model was given no grounding rules at all) and the empty-
retrieval fallback.  Both are `optional`, so blanking them restores exactly the
behaviour that preceded them.
"""

from string import Formatter
from typing import Iterable, Mapping

from app.schemas.prompt import Prompt, PromptGroup, PromptId, PromptVariable

# Rendered between two chunks inside the context block.  Not a prompt: it is
# the block's own punctuation, and an editable separator only invites a broken
# one that nothing would report.
CHUNK_SEPARATOR = "\n\n"

# Similarity scores are formatted before substitution, so a template writes a
# bare {score} and still gets three decimals rather than a raw float.
SCORE_PRECISION = 3


# --- Variables ---------------------------------------------------------------

_CHUNKS = PromptVariable(
    name="chunks",
    description="Every retrieved chunk, each rendered with the chunk format below.",
    required=True,
    example="[Chunk doc-1#4 | score=0.812]\nRevenue for FY2025 was $48.2m…",
)

_CHUNK_VARIABLES: tuple[PromptVariable, ...] = (
    PromptVariable(
        name="content",
        description="The chunk's text, verbatim from the indexed file.",
        required=True,
        example="Revenue for FY2025 was $48.2m, up 14% on the prior year.",
    ),
    PromptVariable(
        name="chunk_id",
        description="Vector id. Include it if you want the model to cite by id.",
        example="doc-1#4",
    ),
    PromptVariable(
        name="score",
        description=f"Similarity score, already rounded to {SCORE_PRECISION} decimals.",
        example="0.812",
    ),
    PromptVariable(
        name="source",
        description="Object key of the file the chunk came from.",
        example="reports/meridian-fy2025.txt",
    ),
    PromptVariable(
        name="document_id",
        description="Id of the document the chunk was split from.",
        example="doc-1",
    ),
    PromptVariable(
        name="rank",
        description="Position in the ranking, best match first, starting at 1.",
        example="1",
    ),
)

_QUERY = PromptVariable(
    name="query",
    description="The question that retrieved nothing.",
    example="What was revenue last year?",
)


# --- Defaults ----------------------------------------------------------------

_SYSTEM_DEFAULT = """\
You answer questions from the context you are given, and nothing else.

- If the context answers the question, answer it directly and name the chunks \
you used, like [Chunk doc-1#4].
- If the context answers it only in part, answer that part and say plainly what \
is missing.
- If the context does not answer it, say so. Do not fall back on what you know.
- Quote figures, dates and names exactly as they appear. Do not round them.
- Be brief. Do not restate the question before answering it.\
"""

_CONTEXT_BLOCK_DEFAULT = """\
Use the following context to answer the user's question:

{chunks}\
"""

_CHUNK_FORMAT_DEFAULT = """\
[Chunk {chunk_id} | score={score}]
{content}\
"""

_NO_CONTEXT_DEFAULT = """\
The search returned no context for this question. Say that the indexed \
documents do not cover it, rather than answering from general knowledge.\
"""


# --- Golden set generator ----------------------------------------------------
#
# These draft the evaluation questions, and they are the one real knob on the
# quality of a generated set.  Everything downstream of them only *checks*: the
# validator can tell a paraphrased answer key from a verbatim one, but it
# cannot make the model ask a better question.  That happens here.

_OUTLINE = PromptVariable(
    name="outline",
    description="Every section title in the document — the only titles a row may cite.",
    required=True,
    example="SECTION 3. FINANCIAL HIGHLIGHTS\nSECTION 4. SEGMENT PERFORMANCE",
)

_COUNT = PromptVariable(
    name="count",
    description="How many questions to write. Sized from the document, not chosen by the model.",
    required=True,
    example="3",
)

_TYPES = PromptVariable(
    name="types",
    description="The question types this pass is allowed to produce.",
    required=True,
    example="lookup, temporal, distractor, synthesis",
)

_SECTION_TITLE = PromptVariable(
    name="section_title",
    description="Title of the section being drafted from.",
    example="SECTION 3. FINANCIAL HIGHLIGHTS",
)

_SECTION_TEXT = PromptVariable(
    name="section_text",
    description="The section's full text, verbatim. Answer keys must be copied from it.",
    required=True,
    example="Total revenue                      2,833.0       2,505.4      +13.1%",
)

_SECTION_FACTS = PromptVariable(
    name="section_facts",
    description="Figures stated in this section, each with the line it sits on.",
    example="- 2,833.0 (SECTION 3. FINANCIAL HIGHLIGHTS): Total revenue 2,833.0 2,505.4 +13.1%",
)

_FACTS = PromptVariable(
    name="facts",
    description="Every figure the whole document states, each with its line and section.",
    required=True,
    example="- 734.1 (SECTION 4. SEGMENT PERFORMANCE): Cold Chain generated revenue of $734.1 million",
)

_GOLDEN_SECTION_DEFAULT = """\
You are drafting evaluation questions from one section of a document. They will
become a golden set: the answer key a retrieval system is scored against. A
wrong answer key marks correct answers wrong forever, so accuracy matters far
more than interest.

The section, verbatim:
---
{section_text}
---

Figures stated in this section:
{section_facts}

The document's outline. These are the only section titles you may cite:
{outline}

Write exactly {count} question(s), using only these types: {types}

  lookup     - one fact stated outright in this section.
  temporal   - a fact that must be told apart from the same fact in another
               period, where the section states both.
  distractor - a fact sitting beside a near-identical wrong one. Name the wrong
               one in forbidden_keys.
  synthesis  - several sentences from this section that must be summarised.

Rules. Every one of these is checked automatically, and a row that breaks one
is sent back for a person to fix:

  - Copy every answer_key character for character from the section above.
    Write "2,833.0", never "2833.0", "$2.8 billion", or "about 2,833".
  - Copy every gold_sections entry exactly from the outline. Cite
    "{section_title}" unless the answer genuinely needs another section too.
  - Give numeric_answer only when the answer is one figure the section states.
    Use numeric_tolerance 0 for whole counts and 0.05 for decimals.
  - For a distractor row, put the nearby wrong value in forbidden_keys, and
    make sure your own answer does not contain it.
  - Your answer must itself contain every answer_key you list. It is scored
    against its own row.
  - Ask nothing this section does not answer.
  - note says in one line what trap the question sets, or "" if it sets none.

Reply with JSON and nothing else:
{{"rows": [{{"type": "lookup", "difficulty": "easy", "question": "...",
"answer": "...", "numeric_answer": null, "numeric_tolerance": null,
"answer_keys": ["..."], "forbidden_keys": [], "gold_sections": ["..."],
"note": ""}}]}}\
"""

_GOLDEN_CROSS_SECTION_DEFAULT = """\
You are drafting evaluation questions that can only be answered by combining
two parts of a document. They will become a golden set: the answer key a
retrieval system is scored against, so every figure must be traceable.

The document's outline. These are the only section titles you may cite:
{outline}

Every figure the document states, with the line it appears on:
{facts}

Write exactly {count} question(s), using only these types: {types}

  multi_hop  - a fact in one section joined to a fact in another. Cite both
               sections.
  arithmetic - a figure the document does NOT state, computed from figures it
               does. Show the working in "derivation".

Rules. Every one is checked automatically:

  - Copy every answer_key character for character from the lines above.
  - Copy every gold_sections entry exactly from the outline. A multi_hop row
    cites at least two.
  - An arithmetic row must carry a derivation, and every operand in it must be
    a figure listed above:
      sum            - the operands added
      difference     - the first, less all the rest
      ratio          - the first divided by the second
      percent_of     - the first divided by the second, times 100
      percent_change - the first less the second, over the second, times 100
    The operator applied to the operands must produce numeric_answer. This is
    recomputed, so working that does not add up is rejected.
  - Do not ask arithmetic whose answer the document already states outright.
    The point is the computation.
  - Your answer prose must contain the computed figure.

Reply with JSON and nothing else:
{{"rows": [{{"type": "arithmetic", "difficulty": "hard", "question": "...",
"answer": "...", "numeric_answer": 0.0, "numeric_tolerance": 0.05,
"answer_keys": ["..."], "gold_sections": ["...", "..."], "note": "",
"derivation": {{"operands": [0.0, 0.0], "operator": "difference",
"explanation": "..."}}}}]}}\
"""

_GOLDEN_UNANSWERABLE_DEFAULT = """\
You are drafting questions a document does NOT answer. They test whether a
retrieval system refuses, or invents a figure to fill the gap. Inventing is the
most damaging failure such a system has, so these are the most valuable rows in
the set.

The document's outline:
{outline}

Figures the document states:
{facts}

Write exactly {count} question(s). Each must ask for something a reader would
reasonably expect this document to hold, and which it does not — a breakdown of
an aggregate it only totals, a figure for a party it names but never quantifies,
a period later than the one it covers.

Rules. Every one is checked automatically:

  - The question must be genuinely unanswerable from the document. Do not ask
    about anything it does state.
  - The answer must say plainly that the document does not state it. Use
    wording like "Not stated." or "The report does not specify", and assert no
    figure of your own.
  - Set "type": "unanswerable" and "must_refuse": true on every row.
  - gold_sections is [] for a fully unanswerable question.
  - note names the tempting wrong answer the document does contain, so a
    reviewer can see what the question is defending against.
  - Make exactly one row half-answerable: it asks two things and the document
    answers only one. For that row, answer the stated half and decline the
    other, list the stated half in answer_keys copied verbatim, and cite the
    section it came from.

Reply with JSON and nothing else:
{{"rows": [{{"type": "unanswerable", "difficulty": "hard", "question": "...",
"answer": "Not stated. ...", "must_refuse": true, "answer_keys": [],
"gold_sections": [], "note": "..."}}]}}\
"""


# --- The registry ------------------------------------------------------------

# Ordered as they appear in an assembled request, so reading the tab top to
# bottom is reading the prompt the model actually receives.
PROMPTS: tuple[Prompt, ...] = (
    Prompt(
        id=PromptId.SYSTEM,
        label="System prompt",
        description=(
            "The standing instruction for every answer — how strictly to stay "
            "inside the retrieved context, and how to cite it."
        ),
        applies_when=(
            "First message on every request, unless the caller sends its own "
            "system prompt. Leave it empty to send none."
        ),
        template=_SYSTEM_DEFAULT,
        default_template=_SYSTEM_DEFAULT,
        variables=[],
        optional=True,
    ),
    Prompt(
        id=PromptId.CONTEXT_BLOCK,
        label="Context block",
        description=(
            "Wraps the retrieved chunks into the message that carries them, "
            "and tells the model what they are for."
        ),
        applies_when="Once per request, whenever retrieval returned at least one chunk.",
        template=_CONTEXT_BLOCK_DEFAULT,
        default_template=_CONTEXT_BLOCK_DEFAULT,
        variables=[_CHUNKS],
    ),
    Prompt(
        id=PromptId.CHUNK_FORMAT,
        label="Chunk format",
        description=(
            "How one retrieved chunk is labelled inside the context block. "
            "What you include here is what the model can cite back."
        ),
        applies_when="Once per retrieved chunk, joined into {chunks} above.",
        template=_CHUNK_FORMAT_DEFAULT,
        default_template=_CHUNK_FORMAT_DEFAULT,
        variables=list(_CHUNK_VARIABLES),
    ),
    Prompt(
        id=PromptId.NO_CONTEXT,
        label="Empty retrieval fallback",
        description=(
            "What the model is told when the search came back with nothing, so "
            "an ungrounded answer is refused rather than improvised."
        ),
        applies_when=(
            "Instead of the context block, when retrieval was asked for and "
            "returned nothing. Never used when RAG is off. Leave it empty to "
            "send none."
        ),
        template=_NO_CONTEXT_DEFAULT,
        default_template=_NO_CONTEXT_DEFAULT,
        variables=[_QUERY],
        optional=True,
    ),
    Prompt(
        id=PromptId.GOLDEN_SECTION,
        group=PromptGroup.GOLDEN,
        label="Section questions",
        description=(
            "Drafts the questions answerable from a single section — the bulk "
            "of a golden set, and where its lookup and distractor rows come from."
        ),
        applies_when="Once per section of the source document, during generation.",
        template=_GOLDEN_SECTION_DEFAULT,
        default_template=_GOLDEN_SECTION_DEFAULT,
        variables=[_SECTION_TEXT, _SECTION_FACTS, _SECTION_TITLE, _OUTLINE, _COUNT, _TYPES],
    ),
    Prompt(
        id=PromptId.GOLDEN_CROSS_SECTION,
        group=PromptGroup.GOLDEN,
        label="Cross-section questions",
        description=(
            "Drafts the questions that need two sections at once, or a figure "
            "the document never states and must be computed."
        ),
        applies_when=(
            "Once per generation run. Arithmetic is dropped from the allowed "
            "types when the document states too few figures to compute from."
        ),
        template=_GOLDEN_CROSS_SECTION_DEFAULT,
        default_template=_GOLDEN_CROSS_SECTION_DEFAULT,
        variables=[_OUTLINE, _FACTS, _COUNT, _TYPES],
    ),
    Prompt(
        id=PromptId.GOLDEN_UNANSWERABLE,
        group=PromptGroup.GOLDEN,
        label="Unanswerable questions",
        description=(
            "Drafts the questions the document does not answer, which are what "
            "catch a model inventing a figure rather than declining."
        ),
        applies_when="Once per generation run.",
        template=_GOLDEN_UNANSWERABLE_DEFAULT,
        default_template=_GOLDEN_UNANSWERABLE_DEFAULT,
        variables=[_OUTLINE, _FACTS, _COUNT],
    ),
)

# Indexed once at import; the registry is static.
_BY_ID: dict[PromptId, Prompt] = {prompt.id: prompt for prompt in PROMPTS}


class UnknownPrompt(LookupError):
    """Raised when an id names no prompt in the registry."""


class InvalidTemplate(ValueError):
    """Raised when a template would not render against the values it is given.

    Caught at save time rather than at render time: a placeholder the pipeline
    cannot fill would otherwise surface as a chat request that dies mid-stream
    with nothing on screen explaining why.
    """


def definition(prompt_id: str) -> Prompt:
    """Return one prompt's shipped definition.

    Args:
        prompt_id: The id to look up.

    Returns:
        The registry entry, whose `template` is still the default.

    Raises:
        UnknownPrompt: If no prompt carries this id.
    """
    try:
        return _BY_ID[PromptId(prompt_id)]
    except ValueError:
        known = ", ".join(entry.value for entry in PromptId)
        raise UnknownPrompt(f"No prompt with id {prompt_id!r}. Known ids: {known}.")


def defaults() -> dict[PromptId, str]:
    """Return every shipped template, keyed by id.

    The fallback the pipeline renders with when no override has been saved —
    and what `prompt_builder` uses when it is called without a prompt set, so
    it stays usable from a test or a batch job with no database behind it.
    """
    return {prompt.id: prompt.default_template for prompt in PROMPTS}


def samples(prompt_id: str) -> dict[str, str]:
    """Return stand-in values for one prompt's variables.

    Used to preview a template, and to test-render an edit before it is saved.
    """
    return {variable.name: variable.example for variable in definition(prompt_id).variables}


def placeholders(template: str) -> list[str]:
    """List the placeholder names a template references.

    Raises:
        InvalidTemplate: If the braces are unbalanced, or a placeholder is
            positional — neither can be filled from a named set of values.
    """
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise InvalidTemplate(f"Unbalanced braces: {exc}. Write a literal brace as {{{{.")

    names: list[str] = []
    for _, field, _, _ in parsed:
        if field is None:
            continue
        # Strip attribute and index access; the base name is what we supply.
        name = field.split(".")[0].split("[")[0]
        if not name.isidentifier():
            raise InvalidTemplate(
                "Placeholders must be named, like {content}. "
                f"Found {'{}' if not name else '{' + name + '}'}."
            )
        names.append(name)
    return names


def validate(prompt_id: str, template: str) -> str:
    """Check that a template can be rendered by the pipeline, and return it.

    Args:
        prompt_id: The prompt being edited.
        template: The replacement text.

    Returns:
        The template unchanged, once it is known to render.

    Raises:
        UnknownPrompt: If the id names no prompt.
        InvalidTemplate: If the text is empty where the prompt requires content,
            names a value the pipeline does not supply, drops a required one, or
            fails to render against the sample values.
    """
    prompt = definition(prompt_id)

    if not template.strip():
        if prompt.optional:
            # Blanking an optional prompt is how you turn it off.
            return ""
        raise InvalidTemplate(f"{prompt.label} cannot be empty.")

    allowed = {variable.name for variable in prompt.variables}
    used = set(placeholders(template))

    unknown = sorted(used - allowed)
    if unknown:
        offered = ", ".join(sorted(allowed)) or "none"
        raise InvalidTemplate(
            f"{prompt.label} has no value for {', '.join(unknown)}. "
            f"Available: {offered}."
        )

    missing = sorted(
        variable.name
        for variable in prompt.variables
        if variable.required and variable.name not in used
    )
    if missing:
        raise InvalidTemplate(
            f"{prompt.label} must include {', '.join('{' + name + '}' for name in missing)}, "
            "or the pipeline would drop it from the request."
        )

    # Last check: render it. Catches a format spec the sample value rejects,
    # such as {score:.2f} against a score already rounded to a string.
    render(template, samples(prompt_id))

    return template


def render(template: str, values: Mapping[str, str]) -> str:
    """Interpolate a template, turning any failure into InvalidTemplate.

    Args:
        template: The text to render.
        values: Values for its placeholders.

    Returns:
        The rendered text.

    Raises:
        InvalidTemplate: If a placeholder is unfilled or a format spec fails.
    """
    try:
        return template.format_map(values)
    except KeyError as exc:
        raise InvalidTemplate(f"Nothing to substitute for {exc}.")
    except (IndexError, ValueError, TypeError) as exc:
        raise InvalidTemplate(f"Template would not render: {exc}")


def ids() -> Iterable[PromptId]:
    """Every prompt id, in the order a request assembles them."""
    return (prompt.id for prompt in PROMPTS)
