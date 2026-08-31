# 00-ai — a RAG pipeline you can watch

A working retrieval-augmented generation system, built so that every stage of
the data flow is visible rather than hidden behind a library.

The project exists to answer a specific question: *where does the data actually
go?* So each stage is a separate module with one job, the two halves of the
pipeline are shown side by side in the UI, and every answer the system gives can
be traced back to the exact chunks that produced it and then judged.

```
UPLOAD          file  ──▶  R2 object storage
INDEX           bytes ──▶  text ──▶ chunks ──▶ embeddings ──▶ Pinecone
ASK             query ──▶  embedding ──▶ similarity search ──▶ prompt ──▶ LLM ──▶ answer
EVALUATE        answer + its chunks ──▶ a verdict per stage ──▶ SQLite ──▶ JSONL
```

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [The three screens](#the-three-screens)
- [How indexing works](#how-indexing-works)
- [How a file is linked to its vectors](#how-a-file-is-linked-to-its-vectors)
- [How staleness is detected](#how-staleness-is-detected)
- [How answers are evaluated](#how-answers-are-evaluated)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Data on disk](#data-on-disk)
- [The offline eval harness](#the-offline-eval-harness)
- [Project layout](#project-layout)

---

## What it does

**Manage a corpus.** Upload `.txt` and `.md` files to Cloudflare R2, replace
them, and see at a glance whether the embeddings still match what is stored.

**Index on demand.** Embedding is a deliberate, separate step. Runs happen
server-side and survive the browser: reload, close the tab, come back later —
the run continues and the page reattaches to its live progress.

**Resume instead of restarting.** A run interrupted at chunk 400 of 500 picks up
where it stopped. Re-indexing an unchanged file embeds nothing at all.

**Answer questions with citations.** Retrieval, prompt assembly and generation
stream to the client as separate events, with the matched chunks, their
similarity scores, and the token cost of the request.

**Judge the answers.** Every chat request is recorded with the chunks that
produced it. Retrieval and generation are then scored *separately*, so
"retrieved the wrong thing" is never confused with "retrieved the right thing
and answered badly".

---

## Architecture

Two processes: a FastAPI backend and a Vite/React frontend. Three external
services, plus two local SQLite files.

```
                       ┌───────────────────────────────┐
   browser  ◀────────▶ │  FastAPI  (127.0.0.1:8000)    │
   (Vite dev :5173)    │                               │
                       │  routers/   HTTP only         │
                       │  services/  all the logic     │
                       │  schemas/   every payload      │
                       └───┬───────┬───────┬───────────┘
                           │       │       │
              ┌────────────┘       │       └──────────────┐
              ▼                    ▼                      ▼
      Cloudflare R2           Pinecone            OpenAI / Anthropic
      (source files)      (chunk vectors)      (embeddings, generation)

                       local, gitignored:
                         backend/data/runs.db    indexing run history
                         backend/data/traces.db  chat traces + verdicts
                         backend/data/logs/      rotating log file
```

**Dependencies run one way only:** `routers → services → schemas → config`.
Routers validate, delegate and serialise — nothing else. Services never import
FastAPI, so every one of them is callable from a script or a test without an
HTTP request. Vendor responses are normalised into our own Pydantic models at
the service boundary, so no SDK type escapes into the application.

### Backend services, by job

| Module | Responsibility |
|---|---|
| `object_store.py` | R2 via the S3 API: list, head, get, put |
| `text_extraction.py` | bytes → text, one extractor per extension |
| `chunker.py` | token-aware splitting with overlap |
| `embeddings.py` | text → vectors, and each model's width |
| `vector_store.py` | Pinecone: upsert, query, list by prefix, fetch, delete |
| `provenance.py` | **the link** between a stored file and its vectors |
| `index_plan.py` | which chunks still need embedding, and which are obsolete |
| `ingestion.py` | one file, end to end, reporting each stage |
| `index_queue.py` | the background worker, the event buffer, subscribe/cancel |
| `index_registry.py` | what is queued and what is in flight |
| `index_catalog.py` | reads the index as a catalog of documents |
| `sync_status.py` | joins storage against the index into one verdict |
| `uploads.py` | the rules governing a write |
| `retrieval.py` | query → embedding → search → ranked chunks |
| `prompt_builder.py` | query + chunks → provider-neutral messages |
| `llm/` | one adapter per provider, behind a factory registry |
| `cost_tracker.py` | token usage → cost, per model |
| `chat_trace.py` | records a chat request as it happens |
| `trace_store.py` | traces and their chunks |
| `evaluation_store.py` | verdicts, withdrawal and restore |
| `evaluation_catalog.py` | the verdicts and reason tags on offer |
| `evaluation_export.py` | the judged set as JSONL |
| `run_store.py` | indexing run history |
| `trace_db.py` | the SQLite connection behind traces |

Adding an LLM provider is an adapter plus one registry entry in
`llm/factory.py` — never an `if` at a call site. Adding a file format is one row
in `text_extraction._EXTRACTORS`.

---

## Getting started

### Prerequisites

- **Python 3.12+** and [uv](https://docs.astral.sh/uv/)
- **Node 20+**
- Accounts for **OpenAI**, **Pinecone**, and **Cloudflare R2**
- Optional: an **Anthropic** key to enable Claude

### 1. Credentials

```sh
cd backend
cp .env.example .env
```

Fill in `.env`:

```ini
OPENAI_API_KEY="sk-..."
PINECONE_API_KEY="pcsk_..."

# Optional — Claude is simply unavailable without it.
ANTHROPIC_API_KEY="sk-ant-..."
# Only needed when the key above is identity-linked (issued to a person
# rather than a workspace). Without it such a key is rejected.
ANTHROPIC_WORKSPACE_ID=""

CLOUDFLARE_ACCOUNT_ID="..."
R2_ACCESS_KEY_ID="..."
R2_SECRET_ACCESS_KEY="..."
R2_BUCKET="your-bucket"
```

The Pinecone index is **created automatically** on first use, at the dimension
the configured embedding model produces. You do not need to pre-create it.

### 2. Run it

```sh
# from the repository root
npm run app:api          # backend  → http://127.0.0.1:8000
```

```sh
cd frontend
npm install
npm run dev              # frontend → http://localhost:5173
```

Interactive API docs at **http://127.0.0.1:8000/docs**.

### 3. First pass

1. Open the **Sources** tab and upload a `.txt` or `.md` file.
2. It appears as **Not indexed**. Press **Index** on the row.
3. Watch the progress panel move through loading → chunking → embedding →
   writing. The row turns **Current**.
4. Click the row to see every chunk that was stored.
5. Go to **Chat**, ask something only that file can answer, and check the
   citations underneath.
6. Judge the answer in the **Evaluate** panel, then find it in **Evaluations**.

### Checks

```sh
cd frontend && npm run build && npm run lint
```

---

## The three screens

### Sources

One row per file, with both sides of the pipeline next to each other: when
storage last changed, when the embeddings were written, how many chunks exist,
and a single verdict.

- **Upload** — drag several files in at once; each reports its own outcome.
- **Replace** — swap the bytes behind a row. Its old vectors are discarded
  immediately, because chunks describing content that no longer exists are worse
  than none: they get cited with full confidence. A differently-named file
  prompts for confirmation rather than being refused.
- **Index / Index stale** — per row, or everything that needs it.
- **Remove vectors** — withdraws a file from retrieval without deleting the
  file. There is deliberately no delete-the-file action; that is the storage
  console's job.
- Expanding a row lists every stored chunk with its vector id and length.

### Chat

- Pick a provider and model from what the backend reports as actually
  configured, rather than a hardcoded list that drifts.
- Retrieval arrives first, so citations and scores render while the answer is
  still streaming.
- Token usage and cost close the stream.
- A failure in retrieval does not kill the answer — it arrives as an `error`
  event and generation continues without context.

### Evaluations

- Every chat request, judged or not, newest first.
- Filter by judged state, verdict, model, or text.
- Expand a row for the full answer, every chunk with its score and source, and
  the judgement history.
- Chunks the score threshold dropped are marked, so a near-miss reads as a
  tuning problem rather than a stage failure.
- Withdrawn judgements stay struck through with a **Restore** link. Only
  *Discard this trace* truly deletes.
- **Export** downloads the judged set as JSONL.

---

## How indexing works

Indexing is deliberately **not** the response to the request that asks for it.

```
POST /sources/index                    →  202 { job_id, accepted, pending, … }
GET  /sources/index/runs               →  what is in flight, if anything
GET  /sources/index/runs/{id}/events   →  SSE: the only streaming endpoint
DELETE /sources/index/runs/{id}        →  stop, and clear the queue
```

**Why the split.** When the work *was* the response, an SSE disconnect cancelled
the generator: reloading the page killed the run mid-file and threw away
embeddings already paid for. It also meant a second Index click aborted the
first stream, and with it the first run. Now the run belongs to the server and
outlives every client.

**One queue, one worker.** A request while a run is in flight *joins that run*
rather than starting a rival. So clicking Index on three rows queues three
files, there is only ever one stream to attach to, and no per-host connection
limit to trip over. `max_index_queue` (default 50) caps how many files may wait;
anything beyond it is refused by name, never silently dropped.

**Reattaching.** Every event carries its cursor in the SSE `id` field. Pass the
last one you saw back as `?after=` and the stream resumes; omit it and the run
replays from the start, which is what a page that just reloaded needs in order
to rebuild its progress. A run whose in-memory buffer has aged out — or one from
before a restart — replays from run history instead.

**Stream events**

| Event | When | Carries |
|---|---|---|
| `started` | once, first | run id, opening file list, embedding model |
| `queued` | on a join | what was added, what is waiting, the new total |
| `progress` | per stage, per file | stage, position, chunk count |
| `completed` | per file | chunks, **chunks reused**, chunks pruned |
| `error` | per failure | which file and stage — the run continues |
| `summary` | once, last | totals, plus each file's re-read status |

`total_files` can grow mid-run, so read it from the latest event rather than
caching the first.

### Resuming rather than restarting

`index_plan.py` compares the chunks the file produces *now* against the text
already in the index. A chunk is already done when the vector at that position
holds identical text and came from the same model.

Comparing the text itself — not a fingerprint of the file — is what makes this
exact: a partially written document can hold chunks from two versions at once,
and no per-file field can tell those apart.

Consequences:
- A run interrupted at chunk 400 of 500 embeds 100 chunks, not 500.
- Re-indexing an unchanged file embeds nothing.
- `force: true` re-embeds everything anyway, for a suspect index.

### The write ordering, and why it matters

**The first chunk is written last.** It carries the fingerprint and the expected
chunk total that the staleness check reads, so writing it last means an
interrupted write leaves the *old* values in place and the file honestly reports
itself stale.

Written first — as it originally was — a half-finished file claimed to be
`current` while its tail served text from a version that no longer existed. That
was reproduced before it was fixed: 18 of 19 stored chunks stale, verdict
`current`.

---

## How a file is linked to its vectors

There is **no join table and no third database**. The link is derived, and
`services/provenance.py` owns every rule:

```
source key            "policies/refunds.md"
   │  sha1, first 16 hex chars
   ▼
document id           "a1b2c3d4e5f6a7b8"
   │  + separator + zero-padded chunk index
   ▼
vector id             "a1b2c3d4e5f6a7b8#00003"
```

And every vector's metadata carries the source key back:

| Key | Purpose |
|---|---|
| `source_key` | the join key — authoritative on both sides |
| `document_id` | derived id, prefixes every vector id |
| `chunk_index` | position within the file |
| `chunk_total` | how many chunks the file should have |
| `content` | the chunk's text, so retrieval returns it directly |
| `source_etag` | the file's content hash **at embed time** |
| `source_last_modified` | the file's timestamp at embed time |
| `embedded_at` | when the vector was written |
| `embedding_model` | which model produced it |

Three properties fall out of this:

- **Re-indexing is idempotent.** The same file always produces the same vector
  ids, so an upsert overwrites in place instead of accumulating duplicates.
- **Deletion is possible on Pinecone serverless**, which has no
  delete-by-metadata-filter: listing by the id prefix produces exactly the id
  list a delete call needs.
- **Orphans are visible.** Enumerating the index reveals vectors whose file is
  gone — something a storage-driven listing can never show.

Timestamps are stored as epoch numbers rather than ISO strings because Pinecone
metadata accepts only strings, numbers, booleans and string lists — and a number
can be range-filtered later.

---

## How staleness is detected

`sync_status.py` joins both sides and reduces each file to one verdict.

| State | Meaning | Resolution |
|---|---|---|
| `not_indexed` | in storage, never embedded | Index it |
| `current` | embeddings match the stored file | nothing to do |
| `stale_content` | the file changed after it was embedded | Re-index |
| `stale_model` | embedded by a different model than is configured now | Re-index |
| `interrupted` | a run stopped partway; only some chunks are indexed | Re-index — only the missing chunks are embedded |
| `orphaned` | vectors exist for a file that is gone | Remove vectors |
| `unsupported` | no extractor for this file type | convert or remove |

Rules are evaluated in precedence order, first match winning, so a file that is
both stale and built by an old model reports the reason that matters most.

**Content, not timestamps.** Staleness is decided by the **etag**. Object
storage bumps `last_modified` on any rewrite — including one that stores
byte-identical content — so a timestamp comparison would report every re-upload
as stale. Both timestamps are still surfaced, because they are what a person
reads; they just do not cast the vote.

**`interrupted` is detected differently.** Every field above describes the
*file*, so none of them can reveal that only some of its chunks were written.
The recorded `chunk_total` can, because the number of vectors present is already
known from the prefix listing. A mismatch in either direction means interrupted:
a write that stopped early, or a prune that never ran. A total of zero means
"cannot tell" — vectors written before this was recorded — and is not treated as
a disagreement.

Two further flags describe what is *happening* rather than what is stored:
`queued` while a file waits its turn, `indexing` while it is actually being
embedded. Both are orthogonal to `state`.

---

## How answers are evaluated

Every chat request is recorded to `traces.db` the moment it is asked — question,
retrieved chunks, answer, cost, timings — whether or not anyone ever judges it.

**Three targets, judged separately:**

| Target | Question it answers |
|---|---|
| `retrieval` | did the search find the right material? |
| `generation` | given that material, was the answer good? |
| `overall` | was this exchange useful? |

Each takes a verdict of `good`, `partial` or `bad`, and each is skippable.
Reason tags are offered only for rows marked below `good`, and are scoped to the
stage they explain — so a retrieval complaint cannot be filed against the
answer.

**Design decisions worth knowing:**

- **Chunk text is stored in full, not by id.** Chunk ids are positional, and a
  re-index at a different chunk size silently repoints them. Storing the id
  alone would mean a verdict slowly detaching from what it was actually about.
- **Evidence and judgement are separate records.** Withdrawing a verdict never
  destroys the trace it was about. Withdrawal is reversible; only discarding the
  trace deletes.
- **Dropped chunks are marked.** A chunk the score threshold filtered out is
  recorded as retrieved-but-dropped, so a near-miss reads as a threshold to tune
  rather than a stage that failed.
- **No third-party tracing tool.** Langfuse was considered and skipped: this
  project exists to show the data flow, which a black box removes.

Unjudged traces are pruned after 30 days. Judged ones are kept indefinitely.

---

## API reference

Full interactive docs at `/docs`. Prose for every endpoint lives in
`backend/app/docs/`, never inline in a route decorator.

### Sources

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/sources` | every file joined with its embeddings; `prefix`, `state` filters |
| `GET` | `/sources/{key}` | one file plus every chunk indexed from it |
| `POST` | `/sources/upload` | store a new file (multipart); `201` new, `200` identical retry, `409` name taken by different content |
| `PUT` | `/sources/{key}` | replace contents and discard the old vectors |
| `DELETE` | `/sources/{key}/index` | remove a file's vectors, keep the file |

### Indexing

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/sources/index` | queue files; `202` with the run id |
| `GET` | `/sources/index/runs` | the live run, then recent history |
| `GET` | `/sources/index/runs/{id}/events` | SSE progress; `?after=` to resume |
| `DELETE` | `/sources/index/runs/{id}` | stop a run and clear its queue |

### Chat

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/chat` | SSE: `retrieval`, then text deltas, then `usage` |
| `GET` | `/chat/models` | provider/model pairs this deployment can actually use |

### Traces and evaluations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/traces` | recorded chat requests; filter by `model`, `state`, `evaluated`, `verdict`, `target`, `source_key`, `search` |
| `GET` | `/traces/{id}` | one trace with its chunks and verdicts |
| `GET` | `/traces/models` | models that appear in the traces |
| `POST` | `/traces/{id}/evaluations` | record a verdict |
| `DELETE` | `/traces/{id}` | discard a trace permanently |
| `GET` | `/evaluations` | every verdict, filterable |
| `GET` | `/evaluations/options` | the verdicts and reason tags on offer |
| `GET` | `/evaluations/export` | the judged set as JSONL |
| `DELETE` | `/evaluations/{id}` | withdraw a verdict (reversible) |
| `POST` | `/evaluations/{id}/restore` | reinstate a withdrawn verdict |

### A note on SSE over POST

`EventSource` in the browser only issues GET requests, and `/chat` is a POST
with a JSON body. So `frontend/src/api/sse.ts` reads the response as a stream
and parses the framing by hand. Four details matter, and getting any of them
wrong corrupts the output *silently* rather than throwing:

1. A payload containing newlines arrives as several `data:` lines that must be
   accumulated and rejoined — not overwritten.
2. Lines are separated by CRLF.
3. Lines beginning with `:` are comments — the server's `: ping` keepalive.
4. A frame with no `event:` line is SSE's default `message` type, which is how
   the chat stream distinguishes answer text from metadata.

---

## Configuration

Environment variables live in `backend/.env`. Provider credentials are read
under their conventional names; everything else takes an `APP_` prefix.

| Setting | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | required |
| `PINECONE_API_KEY` | — | required |
| `ANTHROPIC_API_KEY` | — | required to import; Claude unavailable if unset |
| `ANTHROPIC_WORKSPACE_ID` | `""` | only for identity-linked keys |
| `CLOUDFLARE_ACCOUNT_ID` | `""` | R2 account |
| `R2_ACCESS_KEY_ID` | `""` | |
| `R2_SECRET_ACCESS_KEY` | `""` | |
| `R2_BUCKET` | `00-ai` | |
| `APP_HOST` | `127.0.0.1` | |
| `APP_PORT` | `8000` | |
| `APP_PINECONE_INDEX_NAME` | `rag-index` | created on first use |
| `APP_MAX_UPLOAD_BYTES` | `10485760` | 10 MB |
| `APP_MAX_INDEX_QUEUE` | `50` | files that may wait at once |
| `APP_CORS_ORIGINS` | `[]` | extra exact origins |

**Not in `.env` on purpose:** `embedding_model` (default
`text-embedding-3-small`) lives in `config.py`, because it describes the
*contents* of the Pinecone index rather than the environment. Changing it
invalidates every stored vector and requires a re-index — so that change should
be visible in a diff.

Chunk geometry defaults to 512 tokens with 64 tokens of overlap, measured with
`cl100k_base` so counts match what the embedding API charges for. Both are
per-request overridable on `POST /sources/index`.

---

## Data on disk

Everything under `backend/data/` is gitignored.

| Path | What |
|---|---|
| `data/runs.db` | indexing history: `runs`, `run_files`, `run_events` |
| `data/traces.db` | `traces`, `trace_chunks`, `evaluations` |
| `data/logs/backend.log` | rotating, 5 MB × 5 |

Both databases run in WAL mode, so reading while the server is running is safe.
You will see `-wal` and `-shm` files alongside; leave them alone.

### Inspecting a run

```sh
cd backend
sqlite3 -box -header data/runs.db "
select job_id, state, indexed, failed, total_chunks, total_reused,
       datetime(started_at,'unixepoch','localtime') as started
from runs order by started_at desc limit 10;"
```

What each run did to each file — `reused` is the embedding you did not pay for:

```sh
sqlite3 -box data/runs.db "
select source_key, state, chunk_count, reused, pruned,
       round(finished_at - started_at, 1) as secs
from run_files order by started_at desc limit 20;"
```

The full trace of the last run:

```sh
sqlite3 -box data/runs.db "
select cursor, event, payload from run_events
where job_id = (select job_id from runs order by started_at desc limit 1)
order by cursor;"
```

`litecli data/runs.db` is nicer for browsing — autocomplete, history, formatted
output. Note that Homebrew's sqlite is keg-only, so `sqlite3` resolves to
Apple's build unless you use
`/opt/homebrew/opt/sqlite/bin/sqlite3` or put it on your `PATH`.

**Run states.** `running`, `completed`, `failed`, `cancelled`, and `abandoned` —
the last meaning the server stopped while the run was in flight. It is applied
on the next startup, because a run marked `running` with no process behind it is
a lie a person reading the history would be misled by.

---

## The offline eval harness

`evals/` holds a deterministic harness that runs without the app, against a
synthetic corpus built for retrieval testing rather than realism.

```sh
python evals/run_eval.py evals/predictions/my-run.jsonl --by-type --failures
```

The golden set is 40 questions over
`data/01-meridian-fy2025-annual-report.txt`, spanning single-fact lookup,
arithmetic that must be computed rather than quoted, multi-hop joins, refusal of
unanswerable questions, FY2024-vs-FY2025 disambiguation, and deliberate
distractor traps — including a near-duplicate name pair and figures that collide
numerically.

Supplying `retrieved_sections` in your predictions adds retrieval recall and
precision, which is the split that separates "retrieved the wrong chunk" from
"retrieved the right chunk and answered badly".

See `evals/README.md` for the corpus design, the row format, and the flags.

This harness and the in-app Evaluations tab answer different questions: the
harness scores a pipeline against known answers, while the tab captures human
judgement on real questions nobody wrote a golden answer for.

---

## Project layout

```
.
├── backend/
│   ├── app/
│   │   ├── config.py            settings, one source of truth
│   │   ├── logging_config.py    console + rotating file
│   │   ├── main.py              app, CORS, startup
│   │   ├── docs/                OpenAPI prose, per router
│   │   ├── routers/             HTTP only: validate, delegate, serialise
│   │   ├── schemas/             every request, response and event payload
│   │   └── services/            all the logic, framework-agnostic
│   ├── data/                    SQLite + logs (gitignored)
│   └── .env                     credentials (gitignored)
├── frontend/
│   └── src/
│       ├── api/                 client, types, SSE parser
│       ├── components/          small shared pieces
│       ├── features/
│       │   ├── sources/         the corpus screen
│       │   ├── chat/            ask and evaluate
│       │   └── traces/          the evaluations screen
│       └── hooks/               one hook per data concern
├── data/                        sample corpus
├── docs/                        written notes on the approach
├── evals/                       offline harness, golden set
├── questions/                   design questions worked through
└── CLAUDE.md                    conventions this codebase holds to
```
