"""OpenAPI documentation for the /traces endpoints.

Traces are the evidence half of evaluation: what the pipeline retrieved and what
it answered, captured at the moment it happened.  The prose here exists to make
one thing unambiguous to a client — the chunk text in a trace is a snapshot, not
a live view of the index, and that is deliberate.
"""

from typing import Any

TRACES_TAG = "traces"

# The idea a client needs before any field on these endpoints makes sense.
_SNAPSHOT_DESCRIPTION = """\
Every chat request is recorded, whether or not anyone judges it. A judgement is
made later — often days later — and by then the index may have been re-built, so
what the model was shown has to be written down while it is still true.

The chunks in a trace are therefore **stored verbatim, not referenced**. A vector
id names a position (`{document_id}#{nnnnn}`), so re-indexing the same file at a
different chunk size leaves that id pointing at different text. A trace that
stored only ids would quietly start describing an answer that never happened.

Each chunk also carries a `content_hash`. Comparing it against the chunk now
under the same id is how you tell "this evidence is still current" from "this
evidence has been replaced since I judged it".
"""

LIST_TRACES_DESCRIPTION = f"""\
List recorded chat requests, newest first.

{_SNAPSHOT_DESCRIPTION}

**Filters**

* `evaluated` — `true` for judged requests only, `false` for the backlog.
* `verdict` / `target` — requests carrying a live judgement of that kind.
  Withdrawn judgements never match: a retracted verdict should not keep a
  request in a "rated bad" list.
* `source_key` — requests that retrieved a chunk from one file. The quickest way
  to ask whether a single document is behind a cluster of bad answers.
* `search` — substring match on the question.

Each row carries a `verdicts` rollup — the latest live verdict per target — so a
list can show what an exchange scored without loading its judgements.
"""

GET_TRACE_DESCRIPTION = f"""\
Return one request with its chunks and every judgement made on it.

{_SNAPSHOT_DESCRIPTION}

Chunks are ordered best-match first. Ones marked `dropped` were excluded from
the prompt by the request's `score_threshold` — they are kept because an answer
that failed while the passage it needed sat just under the threshold is a tuning
problem, and that is invisible if the chunk is simply discarded.

Unlike the listing, this includes **withdrawn** judgements: the detail view is
where the history of having changed your mind is worth reading.
"""

DELETE_TRACE_DESCRIPTION = """\
Discard one request, its chunks and its judgements.

A hard delete, and the only one in this API. Withdrawing a judgement keeps the
record; this removes the evidence itself, and is meant for a request that should
never have been recorded — a test, or a question containing something private.
"""

CREATE_EVALUATION_DESCRIPTION = """\
Judge one stage of a recorded request.

`target` is what makes the record useful. "The answer was wrong" cannot be acted
on; "the right chunk was never retrieved" can. Judge `retrieval` and `generation`
separately when they disagree — two judgements on one trace is the normal case,
not an edge case.

`tags` must come from `GET /evaluations/options`, and must belong to the target
being judged. A retrieval reason attached to a generation verdict is rejected
rather than dropped, because silently discarding it would hide the very fault
being recorded.

Judging the same target again does not overwrite the earlier verdict — it adds
another, and the newest live one is what the rollup shows. The older judgement
stays readable in the trace's detail.
"""

GET_TRACE_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "No trace recorded under that id.",
        "content": {
            "application/json": {
                "example": {"detail": "No trace recorded under id 'a1b2c3'."}
            }
        },
    },
}

CREATE_EVALUATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "A tag is unknown, or belongs to a different target.",
        "content": {
            "application/json": {
                "example": {
                    "detail": (
                        "Tag(s) hallucinated do not belong to target 'retrieval'. "
                        "Judge that stage separately instead."
                    )
                }
            }
        },
    },
    404: {
        "description": "No trace recorded under that id — it may have aged out.",
        "content": {
            "application/json": {
                "example": {
                    "detail": (
                        "No trace recorded under id 'a1b2c3'. It may have aged "
                        "out of retention, or the request was never traced."
                    )
                }
            }
        },
    },
}

__all__ = [
    "CREATE_EVALUATION_DESCRIPTION",
    "CREATE_EVALUATION_RESPONSES",
    "DELETE_TRACE_DESCRIPTION",
    "GET_TRACE_DESCRIPTION",
    "GET_TRACE_RESPONSES",
    "LIST_TRACES_DESCRIPTION",
    "TRACES_TAG",
]
