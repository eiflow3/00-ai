"""OpenAPI documentation for the POST /chat Server-Sent Events stream.

OpenAPI has no native way to describe a stream of differently-shaped events, so
this module builds the `responses` object by hand: a `oneOf` over the four event
schemas plus prose covering the ordering guarantees a client can rely on.

It lives apart from the router so the handler stays readable, and it derives
every schema from the models in app.schemas.chat — the documentation cannot
drift from the payloads the endpoint actually sends.
"""

from typing import Any

from pydantic.json_schema import models_json_schema

from app.schemas.chat import (
    ChatStreamErrorEvent,
    ChatStreamMessageEvent,
    ChatStreamRetrievalEvent,
    ChatStreamTraceEvent,
    ChatStreamUsageEvent,
)

# Every event shape the stream can emit, in the order they can occur.
_EVENT_MODELS = (
    ChatStreamTraceEvent,
    ChatStreamErrorEvent,
    ChatStreamRetrievalEvent,
    ChatStreamMessageEvent,
    ChatStreamUsageEvent,
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


_EVENT_UNION_SCHEMA, CHAT_COMPONENT_SCHEMAS = _build_event_schemas()


CHAT_MODELS_DESCRIPTION = """\
List the provider and model pairs this deployment can actually use.

A client should build its model selector from this rather than a hardcoded
list: which providers work depends on the credentials configured here, and a
model missing from the pricing registry answers normally but reports zero cost.

`available: false` means the option cannot be used at all. An available option
may still carry a `detail` — a caveat that does not prevent use but is worth
showing, such as a credential that only some account types require.
"""


# Prose contract: what a client can count on, and in what order.
_STREAM_DESCRIPTION = """\
A `text/event-stream` of SSE events. Emitted in this order:

| Event | Occurrences | Payload |
| --- | --- | --- |
| `trace` | exactly 1, always first | The id this request is being recorded under. |
| `error` | 0 or more | Which pipeline stage failed, and why. |
| `retrieval` | exactly 1 | The chunks that ground the answer, each with its similarity score. |
| *(unnamed)* | 0 or more | One delta of answer text. Has no `event:` line, so it arrives as SSE's default `message` type. |
| `usage` | 0 or 1, always last | Token counts and cost for the request. |

**Guarantees**

* `trace` is the first event on the wire, sent before retrieval runs and before
  anything can fail. Every request is recorded — question, retrieved chunks and
  answer — so that it can be judged later; the id is how a client files that
  judgement (`POST /traces/{trace_id}/evaluations`) and how it reads the
  evidence back (`GET /traces/{trace_id}`). A client that never evaluates can
  ignore it.
* `retrieval` always arrives before the first text delta, so a client can render
  citations while the answer is still streaming. It is sent even when retrieval
  is disabled or matched nothing — with an empty `chunks` list.
* `chunks` are ordered by descending similarity score, already filtered by the
  request's `score_threshold`.
* `error` reports which stage failed. A `retrieval` failure is non-fatal —
  it arrives first and the answer still streams, ungrounded. A `generation`
  failure ends the stream: the HTTP status was already sent when the stream
  opened, so a provider rejecting the request can only be reported this way.
  Failures that occur before the stream opens (such as an unsupported provider)
  are returned as an HTTP error status instead, never as an event.
* `usage` covers the generation call only — the embedding call made during
  retrieval is not priced into it. It is omitted entirely when the provider
  reports no usage data, so clients must not block on it.
* The SSE layer also emits periodic keepalive comment lines (`: ping`). These
  are not events and should be ignored.
"""

# A representative stream, shown in the docs UI.
_STREAM_EXAMPLE = (
    'event: trace\n'
    'data: {"trace_id":"9f2c4a1b8e7d4c2fa0b1c2d3e4f5a6b7"}\n'
    '\n'
    'event: retrieval\n'
    'data: {"query":"What is the refund window?","chunks":[{"chunk_id":"c1",'
    '"document_id":"d7","content":"Refunds are accepted within 30 days.",'
    '"score":0.912,"source":"policy.md"}],"total_searched":5,'
    '"embedding_model":"text-embedding-3-small"}\n'
    '\n'
    'data: Refunds\n'
    '\n'
    'data:  are accepted within 30 days.\n'
    '\n'
    'event: usage\n'
    'data: {"model":"gpt-5.6-terra","input_tokens":412,"output_tokens":18,'
    '"total_cost":0.00104}\n'
    '\n'
)

# The `responses` object consumed by the route decorator.
CHAT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "SSE stream of retrieval results followed by the generated answer.",
        "content": {
            "text/event-stream": {
                "schema": _EVENT_UNION_SCHEMA,
                "example": _STREAM_EXAMPLE,
            }
        },
    },
    400: {
        "description": "Unsupported LLM provider — raised before the stream opens.",
        "content": {
            "application/json": {
                "example": {
                    "detail": "Unsupported LLM provider: 'gemini'. Supported: openai, claude"
                }
            }
        },
    },
}

# The prose lives on the operation itself so it renders above the schema.
CHAT_DESCRIPTION = _STREAM_DESCRIPTION

__all__ = ["CHAT_COMPONENT_SCHEMAS", "CHAT_DESCRIPTION", "CHAT_RESPONSES"]
