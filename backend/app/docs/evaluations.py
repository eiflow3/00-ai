"""OpenAPI documentation for the /evaluations endpoints.

A judgement is a separate record from the request it judges. These endpoints
read and withdraw those judgements; making one belongs to the trace it is about,
and lives under `POST /traces/{trace_id}/evaluations`.
"""

from typing import Any

EVALUATIONS_TAG = "evaluations"

EVALUATION_OPTIONS_DESCRIPTION = """\
List the verdicts and reason chips a client should build its evaluate control
from.

Served rather than hardcoded for the same reason the model list is: if each
client invents its own reason codes, judgements accumulate as free text and
nothing can be counted.

Every tag names the `target` it can explain, and the two sets do not overlap. A
reason that seems to fit either stage is a badly worded tag, not one that should
appear twice — the whole point of the scoping is that the answer to "retrieval
or generation?" falls out of the record instead of being argued about later.
"""

LIST_EVALUATIONS_DESCRIPTION = """\
List judgements across every request, newest first.

Withdrawn judgements are excluded unless `include_deleted` is set. They are
never removed: the request they point at is still evidence, and the fact that a
verdict was retracted is itself worth reading.
"""

WITHDRAW_EVALUATION_DESCRIPTION = """\
Withdraw a judgement, keeping the record.

The row stays, marked `deleted` with the time and any reason given. It stops
counting toward the rollup on its request, stops matching a verdict filter, and
drops out of the export — but it remains visible on the request's detail, which
is where a change of mind is worth seeing.

Use `POST /evaluations/{evaluation_id}/restore` to reinstate it.
"""

RESTORE_EVALUATION_DESCRIPTION = """\
Reinstate a withdrawn judgement, clearing its deletion marks.
"""

EXPORT_DESCRIPTION = """\
Download every judged request as JSONL — one JSON object per line.

Each line carries the question, the answer, every chunk that grounded it (text
included), the settings the request ran under, and the live judgements made on
it. Grouped by pipeline stage rather than flattened, so the file answers the
question it exists for by being read rather than cross-referenced.

Requests with no live judgement are omitted, and so are withdrawn judgements: an
exported dataset should stand for what you think now.

The response streams, so a long history does not have to be built in memory
before the first byte is sent.
"""

EVALUATION_NOT_FOUND_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "No judgement with that id.",
        "content": {
            "application/json": {"example": {"detail": "No evaluation with id 'e1f2'."}}
        },
    },
}

EXPORT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Newline-delimited JSON, one judged request per line.",
        "content": {
            "application/x-ndjson": {
                "example": (
                    '{"schema_version":1,"trace_id":"a1b2","question":"What was FY2025 revenue?",'
                    '"answer":"$2,833.0 million.","retrieval":{"chunk_count":5,'
                    '"chunks":[{"rank":0,"source_key":"annual-report.txt","score":0.91,'
                    '"content":"Total revenue was $2,833.0 million...","dropped":false}]},'
                    '"generation":{"model":"gpt-5.6-terra","total_cost":0.00104},'
                    '"evaluations":[{"target":"generation","verdict":"good","tags":[]}]}\n'
                )
            }
        },
    },
}

__all__ = [
    "EVALUATIONS_TAG",
    "EVALUATION_NOT_FOUND_RESPONSES",
    "EVALUATION_OPTIONS_DESCRIPTION",
    "EXPORT_DESCRIPTION",
    "EXPORT_RESPONSES",
    "LIST_EVALUATIONS_DESCRIPTION",
    "RESTORE_EVALUATION_DESCRIPTION",
    "WITHDRAW_EVALUATION_DESCRIPTION",
]
