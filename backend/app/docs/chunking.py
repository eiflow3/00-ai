"""OpenAPI documentation for the /chunking endpoints.

The catalog, preview and variant endpoints return ordinary response models that
FastAPI documents on its own.  The scoring stream does not: it emits a sequence
of differently-shaped events, which OpenAPI has no native way to express, so
that `responses` object is built here by hand from the event models themselves
— documentation that cannot drift from what the endpoint sends.

It lives apart from the router so the handlers stay readable.
"""

from typing import Any

from pydantic.json_schema import models_json_schema

from app.schemas.variant_score import (
    ScoreErrorEvent,
    ScoreProgressEvent,
    ScoreStartedEvent,
    ScoreSummaryEvent,
    ScoreVariantEvent,
)

# Every event shape the scoring stream can emit, in the order they occur.
_EVENT_MODELS = (
    ScoreStartedEvent,
    ScoreProgressEvent,
    ScoreVariantEvent,
    ScoreErrorEvent,
    ScoreSummaryEvent,
)

# Swagger UI resolves `$ref` only under `#/components/schemas`, so the event
# models must be referenced there rather than through inline `$defs`.
_COMPONENTS_REF_TEMPLATE = "#/components/schemas/{model}"


def _build_event_schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the `oneOf` schema for the stream, and the components it needs."""
    refs, defs = models_json_schema(
        [(model, "validation") for model in _EVENT_MODELS],
        ref_template=_COMPONENTS_REF_TEMPLATE,
    )
    union = {"oneOf": [refs[(model, "validation")] for model in _EVENT_MODELS]}
    return union, defs.get("$defs", {})


_EVENT_UNION_SCHEMA, CHUNKING_COMPONENT_SCHEMAS = _build_event_schemas()


# --- Prose ------------------------------------------------------------------

CHUNKING_TAG = "chunking"

# The idea the whole tag rests on, documented once because every endpoint here
# is meaningless without it.
_VARIANT_DESCRIPTION = """\
A **variant** is one way of cutting a document: a strategy plus the geometry it
ran at, named `{strategy}-{chunk_size}-{chunk_overlap}` — for example
`recursive-512-64`.

Geometry is part of the identity rather than a setting beside it, because the
same splitter at 512 tokens and at 256 retrieves differently enough to be a
separate experiment.

Each variant's vectors live in **their own namespace**, inside an index kept
apart from the one the app answers from. Two consequences follow, and both are
the point:

* a query issued against one variant cannot return another's chunks, so a
  comparison is never contaminated;
* the production index is untouched by anything done here, so an experiment can
  never change a real answer.

The same file can therefore be embedded under as many variants as you like,
all at once, and asked the same question in each.
"""

STRATEGIES_DESCRIPTION = f"""\
List the ways this deployment can cut a document.

Rendered into a picker rather than hardcoded by a client, so a strategy added
to the registry appears without a release on the other side.

Each entry says what the strategy does to text, where it tends to win or lose,
whether `chunk_overlap` affects it at all, and whether cutting with it costs
API calls of its own beyond the embeddings every strategy needs.

{_VARIANT_DESCRIPTION}
"""

PREVIEW_DESCRIPTION = f"""\
Cut a stored file with a strategy and describe the result, **without embedding
anything**.

Nothing is written, no vector is touched and no embedding is paid for. This is
how a strategy is chosen: run the preview, read the shape of the cut, and only
then decide whether it is worth indexing.

The summary matters more than the chunk list. Two strategies at the same
nominal size routinely produce very different numbers of chunks, and that
difference decides more about retrieval than anything visible in the text of
any one chunk:

| Field | What it tells you |
| --- | --- |
| `chunk_count` | How many vectors indexing this would create. |
| `min_tokens` / `median_tokens` / `max_tokens` | How even the cut is. A wide spread means some chunks are far more specific than others. |
| `total_tokens` | What embedding it would measure. |
| `repeated_fraction` | How much of that is overlap — text embedded twice. |

`variant_id` names the variant this configuration *would* create, so a client
can say exactly what pressing Index will produce.

{_VARIANT_DESCRIPTION}
"""

LIST_VARIANTS_DESCRIPTION = f"""\
List every chunking variant that currently holds vectors.

Read back from the vector index itself rather than from a table of experiments,
so the list cannot claim a variant that was deleted from the provider console,
and cannot miss one written by another process. It is correct after a restart
for the same reason.

A variant reporting `interrupted` holds fewer vectors than its last run said
the file should have — a run that stopped partway. Scoring it would blame the
strategy for text that was never embedded, so it is reported rather than
quietly included.

{_VARIANT_DESCRIPTION}
"""

DELETE_VARIANT_DESCRIPTION = """\
Delete a variant and every vector in it.

The experiment's namespace is dropped whole. Nothing in object storage is
touched — the file stays, and indexing it under that variant again rebuilds it.

Deleting a variant that does not exist reports zero rather than failing: that
is the state the caller asked for.
"""

SCORE_DESCRIPTION = """\
Put a golden set to every variant and count the results.

Reading two answers side by side tells you which one you preferred that time.
It does not tell you which way of cutting the document retrieves better, and on
four strategies that are all roughly reasonable it never will. So the same
questions go to every variant, with the same model, the same prompt and the
same `top_k`, and the only thing that differs is where the chunks came from.

**Retrieval recall is the headline, not correctness.** Whether the passage the
answer needed came back is chunking's job; whether the answer reads well is the
model's. A capable model will paper over a mediocre retrieval often enough to
hide a real difference between two strategies.

Which sections a chunk came from is computed from the document at scoring time,
not read off the chunk — only one of the strategies knows what a section is, so
reading its own metadata would measure it against a yardstick the others never
had.

With `variants` empty, every variant holding the set's source file is scored.
With `generate` false, answers are not produced at all: the run measures
retrieval alone, at no model cost.

Deliberately does not stream. Scoring four variants against twenty questions is
minutes of work, so the request only starts the run; progress is read from the
run's own stream, which any client can open, close and reopen.
"""

SCORE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "description": (
            "No variant holds the file, the set has no rows left to score "
            "with, or the provider is not one this deployment can use."
        )
    },
    404: {"description": "No golden set with that id."},
}

ATTACH_SCORE_DESCRIPTION = """\
Follow one scoring run's events, replaying whatever the client missed.

Events arrive in this order:

1. `started` — once, naming the variants and how many questions each will face.
2. `progress` — one per question per variant, carrying that row's result as
   soon as it is known.
3. `variant` — one per variant, when it has finished the whole set.
4. `error` — a variant or a row that failed. The run continues without it.
5. `summary` — once, with every variant ranked by retrieval recall.

Each event carries a cursor in its SSE `id` field. Pass the last one seen as
`after` to resume rather than replay.

A run is held in memory only. There is no history table behind a score, because
a score describes an index at a moment: what a variant retrieved today says
nothing about what it retrieves after the file is re-indexed. A run that has
aged out returns an empty stream, and the answer is to run it again.
"""

ATTACH_SCORE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "SSE stream of the run's events, ending with the ranking.",
        "content": {"text/event-stream": {"schema": _EVENT_UNION_SCHEMA}},
    }
}

STOP_SCORE_DESCRIPTION = """\
Stop a scoring run, keeping whatever it has already measured.

The variants scored before the stop are still reported in the closing summary —
a comparison of two variants out of four is worth less than one of all four,
but it is worth more than nothing.
"""

STOP_SCORE_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such run is still in memory."},
}
