"""OpenAPI documentation for the /prompts endpoints.

These endpoints exist because the wording that decides how an answer is grounded
used to be a constant in the code.  Changing it meant an edit and a redeploy,
which is the wrong shape for the thing you change most often while tuning a
retrieval pipeline.
"""

from typing import Any

PROMPTS_TAG = "prompts"

LIST_PROMPTS_DESCRIPTION = """\
List every prompt the pipeline assembles a request from, in the order they are
sent.

Each entry carries the text in force, the default it shipped with, and the
variables the template may interpolate. A prompt is `edited` only while it
actually differs from its default — one nobody has touched follows the code, so
an improved default arrives without having to be re-accepted.

The variables are the contract, not a description of one: a template naming a
value the pipeline does not supply is refused on save rather than failing
halfway through a stream.
"""

GET_PROMPT_DESCRIPTION = """\
Fetch one prompt as it currently stands, with the default a reset would restore.
"""

UPDATE_PROMPT_DESCRIPTION = """\
Replace one prompt's text.

The template is validated before it is stored: its placeholders must all be
values the pipeline supplies, the required ones must be present, and it must
render against sample values. A template that would break a chat request is
rejected here, where there is a person to read the reason.

Saving the shipped default is treated as a reset rather than an override, so a
prompt goes back to following the code once it matches it again.

Prompts marked `optional` accept an empty template, which turns them off — that
is how the system prompt and the empty-retrieval fallback are removed entirely
rather than sent blank.
"""

RESET_PROMPT_DESCRIPTION = """\
Discard a prompt's override, returning it to the text that ships with the code.

Resetting a prompt that was never edited is not an error: the caller asked for a
state the store is already in, and it is already in it.
"""

PREVIEW_PROMPTS_DESCRIPTION = """\
Render the prompts in force into the exact message list a request would send.

The point of an editor for these is that the assembled request is visible rather
than inferred. A chunk format reads differently on its own than it does repeated
once per retrieved chunk and wrapped in the block that carries them, and the
difference between those two is where the token budget goes.

Set `grounded` to false to preview the path taken when RAG is off, where the
context block and the empty-retrieval fallback are both skipped.
"""

PROMPT_NOT_FOUND_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "No prompt carries this id.",
        "content": {
            "application/json": {
                "example": {
                    "detail": (
                        "No prompt with id 'context'. Known ids: system, "
                        "context_block, chunk_format, no_context."
                    )
                }
            }
        },
    }
}

INVALID_TEMPLATE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **PROMPT_NOT_FOUND_RESPONSES,
    400: {
        "description": "The template would not render in the pipeline.",
        "content": {
            "application/json": {
                "example": {
                    "detail": (
                        "Chunk format has no value for similarity. "
                        "Available: chunk_id, content, document_id, rank, score, source."
                    )
                }
            }
        },
    },
}
