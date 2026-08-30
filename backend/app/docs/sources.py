"""OpenAPI documentation for the /sources endpoints.

The listing endpoints return ordinary response models that FastAPI documents on
its own.  The indexing endpoint does not: it streams a sequence of
differently-shaped events, which OpenAPI has no native way to express.  So this
module builds that `responses` object by hand — a `oneOf` over the event
schemas, plus prose covering the ordering a client can rely on.

It lives apart from the router so the handlers stay readable, and it derives
every schema from the models in app.schemas.ingestion — the documentation
cannot drift from the payloads the endpoint actually sends.
"""

from typing import Any

from pydantic.json_schema import models_json_schema

from app.schemas.ingestion import (
    IndexCompletedEvent,
    IndexErrorEvent,
    IndexProgressEvent,
    IndexStartedEvent,
    IndexSummaryEvent,
)

# Every event shape the indexing stream can emit, in the order they occur.
_EVENT_MODELS = (
    IndexStartedEvent,
    IndexProgressEvent,
    IndexCompletedEvent,
    IndexErrorEvent,
    IndexSummaryEvent,
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


_EVENT_UNION_SCHEMA, SOURCES_COMPONENT_SCHEMAS = _build_event_schemas()


# --- Prose ------------------------------------------------------------------

SOURCES_TAG = "sources"

# How the two sides are joined, documented once here because it is the concept
# a client needs in order to interpret every field on these endpoints.
_RELATIONSHIP_DESCRIPTION = """\
Each file in object storage maps to many vectors in the index. The link is
derived, not stored in a separate table:

* the object key hashes to a stable `document_id`;
* that id prefixes every one of the file's vector ids (`{document_id}#{nnnnn}`);
* the object key itself is written into each vector's metadata.

So a file resolves to its vectors, and any vector resolves back to its file.
Because the ids are derived, re-indexing the same file overwrites its vectors
in place instead of accumulating duplicates.

Every vector also records what the source file looked like **at the moment it
was embedded** — its content hash, its last-modified time, and the embedding
model used. Comparing that snapshot against the live object is what produces
the `state` field.
"""

_STATE_TABLE = """\
| `state` | Meaning | Resolution |
| --- | --- | --- |
| `not_indexed` | The file exists in storage but has never been embedded. | Index it. |
| `current` | The embeddings match the file in storage. | Nothing to do. |
| `stale_content` | The file changed in storage after it was embedded. | Re-index it. |
| `stale_model` | Embedded with a different model than the one configured now. | Re-index it. |
| `orphaned` | Vectors exist for a file that is no longer in storage. | Delete its vectors. |
| `unsupported` | No extractor handles this file type; indexing skips it. | Convert or remove the file. |

Staleness is decided by the **content hash**, not the timestamp. Object storage
updates `last_modified` on any rewrite, including one that stores byte-identical
content, so a timestamp comparison would report every re-upload as stale. Both
timestamps are still returned — `source.last_modified` from storage and
`indexed.embedded_at` from the index — because they are what a person reads.
"""

LIST_SOURCES_DESCRIPTION = f"""\
List every source file alongside the state of its embeddings.

This is the endpoint behind a file list: each row carries the object as it
exists in storage, what the vector index holds for it, and a single verdict
saying whether the embeddings need rebuilding.

{_STATE_TABLE}
The separate `indexing` flag says whether a run is embedding that file *right
now*. It is orthogonal to `state`, which describes what is stored — a file can
read `not_indexed` while its embeddings are being built.

Files that exist only in the index — whose object has since been deleted —
appear at the end of the list as `orphaned`, so nothing indexed is invisible.

{_RELATIONSHIP_DESCRIPTION}"""

GET_SOURCE_DESCRIPTION = """\
Return one file's state together with every chunk indexed from it.

The chunks come back in document order with their vector ids, so a client can
inspect exactly what was embedded and trace any retrieval result back to its
position in the source file.
"""

DEINDEX_DESCRIPTION = """\
Delete a file's vectors from the index, leaving the file itself untouched in
object storage.

This is the resolution for an `orphaned` file, and the way to withdraw a
document from retrieval without deleting the original. Indexing the same key
again restores it.
"""

UPLOAD_DESCRIPTION = """\
Upload a new file into object storage.

The file is stored but **not** indexed — it appears in the list as
`not_indexed`, ready for an indexing run you trigger. Uploading and embedding
stay separate so a batch of files can be added and then embedded in one pass.

Only file types the pipeline can read are accepted; anything else is refused
rather than stored, so the file list never fills with rows indexing will skip.

Uploading the **same file** twice is not an error. The second call returns
`200` with `created: false`, because a client whose connection dropped after the
object was written has no way to know it succeeded — a retry should reach the
state it asked for, not an error about its own earlier attempt. A first upload
returns `201`.

Uploading **different** content to a key that is already taken is refused with
`409`. Overwriting is what `PUT /sources/{source_key}` is for, and it has
different consequences for the existing embeddings.
"""

REPLACE_DESCRIPTION = """\
Replace the contents of an existing file, and discard the vectors built from
the old contents.

Every chunk embedded from the previous version is deleted as part of this call.
Between replacing and re-indexing, the file therefore contributes nothing to
retrieval — which is deliberate: chunks describing content that no longer
exists are worse than none, because they are cited with full confidence.

The key comes from the path, not from the uploaded file's name. A replace
targets an existing entry, so its identity is that entry's whatever the chosen
file happens to be called; a client should confirm an obvious name mismatch
with the user before calling this.

The file is left `not_indexed`. Run the pipeline to embed the new contents.
"""

UPLOAD_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "The file was refused — an unreadable type, an empty file, "
        "or one over the size limit.",
        "content": {
            "application/json": {
                "example": {
                    "detail": "Only .markdown, .md, .txt files can be indexed, so "
                    "other types are not accepted."
                }
            }
        },
    },
    409: {
        "description": "Different content already exists at that key. Replace it "
        "instead. Re-uploading identical content returns 200, not this.",
        "content": {
            "application/json": {
                "example": {
                    "detail": "A different file already exists at 'policy.md'. "
                    "Replace it instead of uploading over it."
                }
            }
        },
    },
}

REPLACE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "The replacement file was refused — an unreadable type, an "
        "empty file, or one over the size limit.",
        "content": {
            "application/json": {"example": {"detail": "The file is empty."}}
        },
    }
}


_INDEX_STREAM_DESCRIPTION = """\
A `text/event-stream` reporting the run's progress. Events arrive in this order:

| Event | Occurrences | Payload |
| --- | --- | --- |
| `started` | exactly 1, always first | Which files the run will process, with which model, and which were skipped as already running. |
| `progress` | 0 or more per file | One pipeline stage finishing: `loading`, `chunking`, `embedding`, `upserting`. |
| `completed` | 0 or 1 per file | That file's chunk count, and how many stale chunks were pruned. |
| `error` | 0 or more | One file failing. The run continues. |
| `summary` | exactly 1, always last | Totals, plus the final state of every processed file. |

**Guarantees**

* `started` always arrives first and names every file in the run, so a client
  can size a progress bar before any work happens.
* A file another run is already embedding is left to that run and listed in
  `started.busy` rather than processed twice. Two runs on one file interleave
  their writes into an index matching neither version, so the second yields.
  Files reported this way are not counted in `total` and produce no further
  events.
* `error` is non-fatal and scoped to one file: an unreadable upload or a failed
  embedding call does not abort the remaining files. Failures that occur before
  the stream opens are returned as an HTTP error status instead, never as an
  event.
* A file reported by `error` never also reports `completed` as indexed — except
  for an unsupported file type, which reports `completed` with `skipped: true`
  followed by an `error` explaining what was skipped.
* `summary.statuses` is re-read from storage and the index after the run, not
  asserted from what was written, so a client can refresh its file list from it
  directly.
* The SSE layer also emits periodic keepalive comment lines (`: ping`). These
  are not events and should be ignored.
"""

INDEX_SOURCES_DESCRIPTION = f"""\
Run the data embedding pipeline: load each file from object storage, split it
into overlapping chunks, embed them, and write them to the vector index with
the provenance that makes staleness detectable.

With no `keys`, the run covers everything under `prefix` that needs it — which
is the "re-index what is stale" case. Naming `keys` explicitly targets just
those files, whatever their current state.

Chunks that a shrinking file no longer produces are pruned, so stale text
cannot keep surfacing in retrieval results.

{_INDEX_STREAM_DESCRIPTION}
{_RELATIONSHIP_DESCRIPTION}"""


# A representative stream, shown in the docs UI.
_INDEX_STREAM_EXAMPLE = (
    "event: started\n"
    'data: {"keys":["policy.md"],"total":1,'
    '"embedding_model":"text-embedding-3-small"}\n'
    "\n"
    "event: progress\n"
    'data: {"source_key":"policy.md","stage":"chunking","file_number":1,'
    '"total_files":1,"chunk_count":4}\n'
    "\n"
    "event: completed\n"
    'data: {"source_key":"policy.md","chunk_count":4,"pruned":0,'
    '"skipped":false,"state":"current"}\n'
    "\n"
    "event: summary\n"
    'data: {"indexed":1,"skipped":0,"failed":0,"total_chunks":4,'
    '"total_pruned":0,"statuses":[]}\n'
    "\n"
)


INDEX_SOURCES_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": _INDEX_STREAM_DESCRIPTION,
        "content": {
            "text/event-stream": {
                "schema": _EVENT_UNION_SCHEMA,
                "example": _INDEX_STREAM_EXAMPLE,
            }
        },
    },
    400: {
        "description": "The request could not be run — for example a chunk overlap "
        "that is not smaller than the chunk size.",
        "content": {
            "application/json": {
                "example": {
                    "detail": "chunk_overlap (512) must be smaller than "
                    "chunk_size (512); otherwise chunking cannot advance."
                }
            }
        },
    },
}

GET_SOURCE_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "No such file in object storage, and nothing indexed under "
        "that key either.",
        "content": {
            "application/json": {"example": {"detail": "No source at key: policy.md"}}
        },
    }
}
