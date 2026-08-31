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

from app.schemas.prompt import Prompt, PromptId, PromptVariable

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
