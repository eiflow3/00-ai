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
    IndexQueuedEvent,
    IndexStartedEvent,
    IndexSummaryEvent,
)

# Every event shape the indexing stream can emit, in the order they occur.
_EVENT_MODELS = (
    IndexStartedEvent,
    IndexQueuedEvent,
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
| `interrupted` | A previous run stopped partway, so only some of the file's chunks are indexed. | Re-index it; only the missing chunks are embedded. |
| `orphaned` | Vectors exist for a file that is no longer in storage. | Delete its vectors. |
| `unsupported` | No extractor handles this file type; indexing skips it. | Convert or remove the file. |

`interrupted` is detected differently from the rest. Every vector records how
many chunks the whole file should have, so comparing that against the number of
vectors actually present reveals a write that stopped early or a prune that
never ran. Without it such a file would report `current`: its first chunk
carries the right content hash, while its tail still holds text from a version
that no longer exists.

Staleness is decided by the **content hash**, not the timestamp. Object storage
updates `last_modified` on any rewrite, including one that stores byte-identical
content, so a timestamp comparison would report every re-upload as stale. Both
timestamps are still returned — `source.last_modified` from storage and
`indexed.embedded_at` from the index — because they are what a person reads.
"""

_CACHING_DESCRIPTION = """\
### Freshness

The two sides of this endpoint are not read the same way. Object storage is
listed **live on every request**, so a file added or deleted straight from the
bucket — the R2 console included — is visible immediately. The vector index is
the expensive side, so what it holds is cached.

A cached read is discarded as soon as any of these says it is out of date:

* **this application wrote something** — an upload, a replace, a deletion, or a
  file finishing an indexing run — which invalidates instantly;
* **the index itself moved** — checked cheaply on every request against the
  index's own total vector count for the listing, and against the file's vector
  ids for a single file. This is what catches vectors added or removed directly
  in the Pinecone console;
* **the entry expired**, after a short TTL.

The TTL is the backstop for the one change nothing else can see: an edit made
directly on a provider console that leaves the vector count and the id set
exactly as they were, such as rewriting one chunk's text in place. Working
directly on either console is outside the contract these endpoints keep — the
application is the only writer that can invalidate instantly.

Pass `refresh=true` to skip the cache and rebuild from the index. That is the
escape hatch after changing something on a provider console, and the way to
confirm what is really stored rather than what was last read.
"""

LIST_SOURCES_DESCRIPTION = f"""\
List every source file alongside the state of its embeddings.

This is the endpoint behind a file list: each row carries the object as it
exists in storage, what the vector index holds for it, and a single verdict
saying whether the embeddings need rebuilding.

{_STATE_TABLE}
Two separate flags say what is *happening* to the file, as opposed to what is
stored: `queued` while it waits its turn, and `indexing` while it is actually
being embedded. Both are orthogonal to `state` — a file can read `not_indexed`
while its embeddings are being built.

Files that exist only in the index — whose object has since been deleted —
appear at the end of the list as `orphaned`, so nothing indexed is invisible.

{_CACHING_DESCRIPTION}
{_RELATIONSHIP_DESCRIPTION}"""

GET_SOURCE_DESCRIPTION = f"""\
Return one file's state together with every chunk indexed from it.

The chunks come back in document order with their vector ids, so a client can
inspect exactly what was embedded and trace any retrieval result back to its
position in the source file.

{_CACHING_DESCRIPTION}"""

DEINDEX_DESCRIPTION = """\
Delete a file's vectors from the index, leaving the file itself untouched in
object storage.

This is the resolution for an `orphaned` file, and the way to withdraw a
document from retrieval without deleting the original. Indexing the same key
again restores it. To remove the file as well, use
`DELETE /sources/{source_key}`.

Refused with `409` while an indexing run is embedding the file or has it
queued — deleting the vectors a worker is halfway through writing would leave
the index holding part of a document with nothing to say so.
"""

DELETE_SOURCE_DESCRIPTION = """\
Delete a file from object storage **and** every vector built from it.

The hard delete. `DELETE /sources/{source_key}/index` is the softer one — it
withdraws a document from retrieval while leaving the file, so indexing the key
again restores it. This leaves nothing behind on either side.

The embeddings go first. If the storage delete then fails, what is left is a
file with no vectors: it reads as `not_indexed` and a re-index repairs it. The
other order can leave vectors describing a file that no longer exists, and a
model cites those with full confidence.

Deleting a key that is already gone from both sides is **not** an error. It
comes back with `vectors_deleted: 0` and `file_deleted: false`, because the end
state the caller asked for is the state the store is in.

Refused with `409` while an indexing run is embedding the file or has it
queued: a worker mid-write would otherwise finish writing vectors for a
document that has just been deleted. Stop the run first.
"""

DELETE_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "An indexing run is holding this file. Stop the run, then "
        "delete it.",
        "content": {
            "application/json": {
                "example": {
                    "detail": "'policy.md' is being embedded right now. "
                    "Stop the run first, then delete it."
                }
            }
        },
    },
}

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
A `text/event-stream` reporting one run's progress. Events arrive in this order:

| Event | Occurrences | Payload |
| --- | --- | --- |
| `started` | exactly 1, always first | The run's id, the files it opened with, and the embedding model. |
| `queued` | 0 or more | Files added to this run after it began, because a later request joined it. |
| `progress` | 0 or more per file | One pipeline stage finishing: `loading`, `chunking`, `embedding`, `upserting`. |
| `completed` | 0 or 1 per file | That file's chunk count, how many chunks were reused without re-embedding, and how many were pruned. |
| `error` | 0 or more | One file failing. The run continues. |
| `summary` | exactly 1, always last | Totals, plus the final state of every processed file. |

**Re-attaching**

Every event carries an SSE `id` field holding its cursor. Pass the last one you
saw back as `after` and the stream resumes from there; omit it and the run
replays from the beginning, which is what a client that has just reloaded wants
in order to rebuild its progress state.

A run whose live buffer has aged out — or one from before the server restarted —
is replayed from run history instead. The events are the same either way.

**Guarantees**

* `started` always arrives first and carries the run's id, so a client can
  re-attach later without having to ask which run is going.
* `total` can grow. One worker drains one queue, so pressing Index again during
  a run adds to the work in flight rather than starting a rival run; each
  addition arrives as a `queued` event. A progress bar should read `total_files`
  from the latest event rather than caching the first.
* `error` is non-fatal and scoped to one file: an unreadable upload or a failed
  embedding call does not abort the remaining files. Failures that occur before
  the run is accepted are returned as an HTTP error status on the enqueue call
  instead, never as an event.
* A file reported by `error` never also reports `completed` as indexed — except
  for an unsupported file type, which reports `completed` with `skipped: true`
  followed by an `error` explaining what was skipped.
* `completed.reused` counts chunks the index already held, identical and from
  the same model, which were therefore not embedded again. A non-zero value
  means an interrupted run was resumed rather than repeated.
* `summary.statuses` is re-read from storage and the index after the run, not
  asserted from what was written, so a client can refresh its file list from it
  directly.
* The SSE layer also emits periodic keepalive comment lines (`: ping`). These
  are not events and should be ignored.
"""

INDEX_SOURCES_DESCRIPTION = f"""\
Queue files for embedding, and return the run they joined.

This endpoint **does not stream**. It accepts work and returns immediately; the
run's progress is read from `GET /sources/index/runs/{{job_id}}/events`.

That separation is the point. When the work was the response, a client that
reloaded cancelled the run mid-file and lost embeddings it had already paid for,
and a second request aborted the first. Now the run belongs to the server: it
outlives every client, and any client can open, close and reopen its stream.

One worker drains one queue, so a request while a run is in flight **joins that
run** rather than starting another. The response says which run the files joined.

With no `keys`, the run covers everything under `prefix` that needs it — which
is the "re-index what is stale" case. Naming `keys` explicitly targets just
those files, whatever their current state.

**What the response tells you**

* `accepted` — files added to the queue.
* `already_queued` — files that were already waiting or being embedded. Not an
  error: the work is going to happen, so the request is simply redundant.
* `rejected` — files refused because the queue is full. Named rather than
  silently dropped, with `limit` giving the ceiling so a client need not
  hardcode it.
* `missing` — named keys with no object behind them.

**What a run does to each file**

Chunks the index already holds, identical and from the same model, are not
embedded again — so an interrupted run resumes rather than starting over. Chunks
a shrinking file no longer produces are pruned, so stale text cannot keep
surfacing in retrieval results.

{_RELATIONSHIP_DESCRIPTION}"""


LIST_RUNS_DESCRIPTION = """\
List the indexing run in flight, if there is one, followed by recent history.

This is the question a client asks on load: *is anything being indexed right
now?* A live run comes back first, carrying the `job_id` needed to attach to its
stream — which is how progress survives a reload.

History is read from disk, so this still answers after a restart. A run that was
in flight when the server stopped reads `abandoned`: the process that owned it is
gone, so it neither completed nor failed, and saying so is more honest than
leaving it marked `running`.
"""

STOP_RUN_DESCRIPTION = """\
Stop a run, discarding whatever was still queued.

Needed because a run no longer dies with the tab that started it. Without this,
a large run begun by mistake could only be waited out or killed with the server.

The file being embedded when Stop arrives may be left partially written. That is
recorded rather than hidden: the file will report `interrupted`, and re-indexing
it embeds only the chunks that are missing.
"""

ATTACH_RUN_DESCRIPTION = f"""\
Stream one run's progress, replaying whatever the client missed.

The only streaming endpoint here. A run just started and a run being returned to
after a reload are read the same way, so there is one framing path rather than
two that have to behave identically.

{_INDEX_STREAM_DESCRIPTION}"""


# A representative stream, shown in the docs UI.
_INDEX_STREAM_EXAMPLE = (
    "id: 0\n"
    "event: started\n"
    'data: {"job_id":"9f1c2ab4de7f5061","keys":["policy.md"],"total":1,'
    '"embedding_model":"text-embedding-3-small"}\n'
    "\n"
    "id: 1\n"
    "event: progress\n"
    'data: {"source_key":"policy.md","stage":"chunking","file_number":1,'
    '"total_files":1,"chunk_count":4}\n'
    "\n"
    "id: 2\n"
    "event: completed\n"
    'data: {"source_key":"policy.md","chunk_count":4,"reused":3,"pruned":0,'
    '"skipped":false,"state":"current"}\n'
    "\n"
    "id: 3\n"
    "event: summary\n"
    'data: {"indexed":1,"skipped":0,"failed":0,"total_chunks":4,'
    '"total_reused":3,"total_pruned":0,"statuses":[]}\n'
    "\n"
)


INDEX_SOURCES_RESPONSES: dict[int | str, dict[str, Any]] = {
    202: {
        "description": "The files were queued. Nothing has been embedded yet — "
        "open the run's event stream to follow it.",
        "content": {
            "application/json": {
                "example": {
                    "job_id": "9f1c2ab4de7f5061",
                    "accepted": ["policy.md"],
                    "already_queued": [],
                    "rejected": [],
                    "missing": [],
                    "limit": 50,
                    "pending": ["policy.md"],
                }
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

ATTACH_RUN_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": _INDEX_STREAM_DESCRIPTION,
        "content": {
            "text/event-stream": {
                "schema": _EVENT_UNION_SCHEMA,
                "example": _INDEX_STREAM_EXAMPLE,
            }
        },
    },
}

STOP_RUN_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "No such run is still held in memory. A run that finished "
        "long ago is in history rather than stoppable.",
        "content": {
            "application/json": {
                "example": {"detail": "No such run: 9f1c2ab4de7f5061"}
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
