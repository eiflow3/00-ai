"""OpenAPI documentation for the /golden endpoints.

Most of these return ordinary response models that FastAPI documents on its
own.  The generation stream does not: it emits a sequence of differently-shaped
events, which OpenAPI has no native way to express.  So that `responses` object
is built here by hand — a `oneOf` over the event schemas, plus prose about the
ordering a client can rely on.

Derived from the models in app.schemas.golden rather than written out, so the
documentation cannot drift from the payloads the endpoint actually sends.
"""

from typing import Any

from pydantic.json_schema import models_json_schema

from app.schemas.golden import (
    GoldenErrorEvent,
    GoldenRowEvent,
    GoldenStageEvent,
    GoldenStartedEvent,
    GoldenSummaryEvent,
)

GOLDEN_TAG = "golden"

# Every event shape the generation stream can emit, in the order they occur.
_EVENT_MODELS = (
    GoldenStartedEvent,
    GoldenStageEvent,
    GoldenRowEvent,
    GoldenErrorEvent,
    GoldenSummaryEvent,
)

# Swagger UI resolves `$ref` only under `#/components/schemas`, so the event
# models must be referenced there rather than through inline `$defs`.
_COMPONENTS_REF_TEMPLATE = "#/components/schemas/{model}"


def _build_event_schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the `oneOf` schema for the stream, and the components it needs.

    FastAPI only registers models it discovers on a route signature, and these
    are never a plain response body — so the caller must merge the returned
    component schemas into the OpenAPI document itself.
    """
    refs, defs = models_json_schema(
        [(model, "validation") for model in _EVENT_MODELS],
        ref_template=_COMPONENTS_REF_TEMPLATE,
    )
    union = {"oneOf": [refs[(model, "validation")] for model in _EVENT_MODELS]}
    return union, defs.get("$defs", {})


_EVENT_UNION_SCHEMA, GOLDEN_COMPONENT_SCHEMAS = _build_event_schemas()


LIST_OPTIONS_DESCRIPTION = """\
The question types, difficulties and validator checks a golden set is built
from.

Served rather than hard-coded for the same reason the evaluation tags are: a
row filed under a type the harness does not bucket is a row whose score nobody
reads, and a client that invents its own labels drifts the moment a type is
added here.
"""

START_RUN_DESCRIPTION = """\
Start drafting a golden set from one source file, and return the id to follow
it by.

Returns `202` immediately. Drafting is a dozen model calls over a couple of
minutes, and doing that inside this response would mean a reloaded tab threw
the work away — so the run is a task, and progress comes from
`GET /golden/runs/{job_id}/stream`, which any client can open and reopen.

The run reads the file straight from object storage, so a source does **not**
have to be indexed first. Indexing decides what can be retrieved; a golden set
is about what the document says.

How many questions of each kind are asked is computed from the document, not
chosen by the caller: section length sets the per-section quota, and a document
that states too few figures is not asked for arithmetic at all. `density`
nudges those quotas without overriding the rule that keeps a model from padding
to a number.
"""

RUN_STREAM_DESCRIPTION = """\
Follow a generation run as it drafts.

Server-sent events. Each event carries the run's cursor as its SSE `id`; pass
the last one you saw back as `after` and the stream resumes there rather than
replaying everything. That is what makes a closed tab survivable.

Event order:

  1. `started` — once, naming the set being filled and the model drafting it.
  2. `stage` — as each stage advances: `extract`, `segment`, `facts`, then one
     per section during `draft`, then `validate` and `self_check`.
  3. `row` — one per drafted row, once it has been validated and stored.
  4. `error` — a pass that failed. **Not** fatal unless `fatal` is true: a
     failed section is reported and the run carries on, because eleven drafted
     sections are worth more than an aborted run.
  5. `summary` — once, with the totals.

A run that has finished and aged out of memory closes the stream immediately.
The set it left behind is the record at that point, so read it with
`GET /golden/sets/{set_id}`.
"""

GET_RUN_DESCRIPTION = """\
Where a generation run stands, without opening the stream.

Only runs still held in memory are reported. Once one ages out, the set it
produced is the better record of what happened — it holds every row, not just
the fact that a run occurred.
"""

STOP_RUN_DESCRIPTION = """\
Stop a run in flight.

The set it was filling is marked failed and keeps whatever rows had already
been stored. Needed because a run no longer dies with the tab that started it.
"""

LIST_SETS_DESCRIPTION = """\
Every golden set, newest first, with its row counts.

Withdrawn sets are excluded unless `include_deleted` is set. A set is soft
deleted so that a run scored against it can still be explained afterwards.
"""

GET_SET_DESCRIPTION = """\
One golden set with all of its rows, in export order.

Each row carries what the harness will read and what it will not: the
validator's findings, the model's working for a computed figure, and whether a
person has accepted the row. Those stop at the export boundary.

A row whose `status` is `flagged` failed at least one check and its `issues`
say which. Flagged rows are kept rather than dropped — most are a good question
with one bad field.
"""

UPDATE_SET_DESCRIPTION = """\
Rename the file a set exports as.

The slug defaults to the source filename with its ordering prefix stripped,
which is rarely what you want the answer key called.
"""

UPDATE_ROW_DESCRIPTION = """\
Edit one row, or record a decision about it.

Every field is optional and only what you send is changed, so accepting a row
and rewriting its answer can arrive through the same endpoint without either
erasing the other.

An edit that touches the row's content re-runs every validator check against
the source document, so fixing a flagged field clears the flag in the same
response. Recording a review decision alone does not.

Rows are addressed by `row_id`, never by the `Q001` id: dropping a row
renumbers everything after it, so an API keyed on the exported number would
silently retarget pending edits.
"""

EXPORT_SET_DESCRIPTION = """\
Download a set as the JSONL file the offline harness reads.

One JSON object per line, in the field order the hand-written
`evals/golden/meridian-fy2025.jsonl` uses, so a generated set and a
hand-written one are interchangeable. Optional fields are omitted rather than
sent as null.

Rows a person dropped are excluded, and the remaining ids are contiguous.
Rows still pending review are included — the file is a draft until someone says
otherwise, and withholding it would mean nobody could try it in the harness.

Save it to `evals/golden/<slug>.jsonl` and score against it with:

    python evals/run_eval.py <predictions>.jsonl --golden evals/golden/<slug>.jsonl
"""

DELETE_SET_DESCRIPTION = """\
Withdraw a set, keeping it readable.

Soft, like withdrawing an evaluation. A set that was used to score a run must
still be readable when someone asks what that run was measured against.
"""

RESTORE_SET_DESCRIPTION = "Undo a withdrawal, putting the set back in the listing."


_NOT_FOUND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
}

SET_NOT_FOUND_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "No golden set has that id.",
        "content": {"application/json": {"schema": _NOT_FOUND_SCHEMA}},
    }
}

ROW_NOT_FOUND_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": (
            "No such set, no such row within it, or the source file it would "
            "be re-checked against is gone."
        ),
        "content": {"application/json": {"schema": _NOT_FOUND_SCHEMA}},
    }
}

START_RUN_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "No file exists at that object key.",
        "content": {"application/json": {"schema": _NOT_FOUND_SCHEMA}},
    },
    400: {
        "description": (
            "The file cannot be read, holds no text, or the named provider is "
            "not one this deployment offers."
        ),
        "content": {"application/json": {"schema": _NOT_FOUND_SCHEMA}},
    }
}

RUN_STREAM_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "A server-sent event stream of the run's progress.",
        "content": {"text/event-stream": {"schema": _EVENT_UNION_SCHEMA}},
    }
}

EXPORT_SET_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The set as newline-delimited JSON, one row per line.",
        "content": {
            "application/x-ndjson": {
                "schema": {"type": "string", "format": "binary"},
                "example": (
                    '{"id": "Q001", "type": "lookup", "difficulty": "easy", '
                    '"question": "What was total revenue in fiscal 2025?", '
                    '"answer": "$2,833.0 million.", "numeric_answer": 2833.0, '
                    '"numeric_tolerance": 0.5, "answer_keys": ["2,833.0"], '
                    '"gold_sections": ["SECTION 3. FINANCIAL HIGHLIGHTS"]}\n'
                ),
            }
        },
    },
    **SET_NOT_FOUND_RESPONSES,
}

STOP_RUN_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "No such run is still in memory.",
        "content": {"application/json": {"schema": _NOT_FOUND_SCHEMA}},
    }
}
