# Migration plan — chunk text out of Pinecone

Today every chunk's text is stored twice: once in R2 as part of the original
file, and once in Pinecone as vector metadata under `content`. Pinecone is
carrying it because retrieval needs the text back alongside a match, and there
was nowhere else to put it.

This plan moves that text into a local database. **Pinecone keeps the vectors,
the ids, and the provenance fields; it loses only `content`.**

Nothing here is a flag day. The migration runs in four phases, each
independently deployable and reversible, and reads do not move until the new
store is proven to hold everything the old one did.

---

## 1. What changes

```
BEFORE
  Pinecone vector
    id       a1b2c3d4e5f6a7b8#00003
    values   [0.021, -0.118, ...]
    metadata source_key, document_id, chunk_index, chunk_total,
             source_etag, source_last_modified, embedded_at,
             embedding_model, content ←── the whole chunk of text

AFTER
  Pinecone vector
    id       a1b2c3d4e5f6a7b8#00003
    values   [0.021, -0.118, ...]
    metadata source_key, document_id, chunk_index, chunk_total,
             source_etag, source_last_modified, embedded_at,
             embedding_model

  index.db  indexed_chunk
    vector_id   a1b2c3d4e5f6a7b8#00003
    source_key  policies/refunds.md
    chunk_index 3
    content     "...the whole chunk of text..."
```

The vector id is the join key, and it is already the chunk id on both sides —
`provenance.py` made the id one identity across the whole system, so this
migration needs no new correlation scheme.

---

## 2. What this buys, honestly

**The real prize is a capability, not a speed-up.** Chunk text in a queryable
database makes **hybrid retrieval** possible — SQLite FTS5 keyword search
alongside vector similarity. That is not available at any price while the text
lives in vector metadata, and it is the single strongest reason to do this.

Secondary, and genuine:

- **Pinecone storage cost falls.** Metadata is stored and billed; chunk text is
  by far the largest field on every vector.
- **The per-vector metadata ceiling stops mattering.** Pinecone caps metadata
  per vector (~40KB — check current limits). Today that silently caps how large
  a chunk may be. Afterwards it does not apply to content at all.
- **`GET /sources/{key}` becomes a local read**, ~0.29s warm → effectively free,
  and its Redis cache entry becomes deletable.
- **Smaller query responses.** Retrieval stops pulling every matched chunk's
  full text back over the wire from Pinecone.

**What it does not buy:** retrieval latency. The text arrives free with the
query response today; afterwards it is a local lookup of ~0.06ms. Roughly
neutral. Do not sell this as making chat faster.

---

## 3. What stays in Pinecone, and why

Only `content` moves. Every other metadata field stays exactly where it is.

That is deliberate. Those fields are small and fixed-size, so they cost almost
nothing, and keeping them preserves three properties that are expensive to
rebuild:

- **Vectors stay self-describing.** A vector still names its source file, its
  position, and the model that made it. Someone looking at the Pinecone console
  can still tell what they are looking at.
- **Orphan detection is unchanged.** `list_indexed_documents()` still walks the
  index and reads provenance from one vector per document.
- **The interrupted-run check is unchanged.** `chunk_total` stays stamped on
  every vector, compared against the count actually present.

The consequence worth stating plainly: **if `index.db` were lost, the vectors
would survive and still be identifiable, but their text would be gone and every
affected file would need re-embedding.** R2 still holds the originals, so this
is a cost event, not data loss. Back up `index.db`.

---

## 4. Schema

A new database, `backend/data/index.db`. Its own file for the same reason
`prompts.db` is: **its retention rule is never.** `runs.db` prunes at thirty
days and `traces.db` prunes with it — chunk text must not share a database with
anything that deletes on a timer.

```sql
CREATE TABLE IF NOT EXISTS indexed_chunk (
    vector_id   TEXT PRIMARY KEY,
    source_key  TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS indexed_chunk_source
    ON indexed_chunk (source_key, chunk_index);
```

`PRAGMA journal_mode=WAL` — one writer (the indexing worker), many concurrent
readers (the chat path). Same pattern as the other three databases.

**Not stored, because they are derived** — storing them invites drift:

- `document_id` — `sha1(source_key)[:16]`, and already the vector id's prefix.
- `char_count` — `len(content)`.
- `chunk_count` — `COUNT(*)` grouped by `source_key`.

### Later, in the same database

The document-grain projection discussed separately (`indexed_document`, one row
per file) belongs in this same file. It is not part of this plan, but the
database is named `index.db` rather than `chunks.db` so it has somewhere to go.

---

## 5. Ordering rules

Two rules govern every write, and both follow from one question: *can a query
ever return a match whose text is missing?*

**On write — content first, vector second.** A vector is queryable the moment
it is upserted. If Pinecone were written first there would be a window where
retrieval returns a match with no text. Written the other way, a failure
between the two leaves a text row whose vector does not exist — invisible to
every read path, and cleaned up by reconciliation.

**On delete — vector first, content second.** This preserves the existing rule
in `deletion.py`: vectors-first, so a failure leaves a file that reads as
`not_indexed` and is repairable by re-indexing, rather than vectors citing a
document that is gone. A leftover text row is harmless and reconcilable.

**The existing "chunk 0 written last" trick is untouched.** It orders the
Pinecone upsert so the vector carrying provenance lands only after the rest of
its version. Text rows go in one SQLite transaction before any of that begins,
so the trick keeps working exactly as it does now.

---

## 6. The three readers

| Reader | Today | After |
|---|---|---|
| `retrieval._to_retrieved_chunk` | reads `content` off the match | looks text up by `vector_id` |
| `index_catalog._to_chunks` | reads `content` off each fetched vector | reads rows by `source_key` |
| `index_plan._matches` | compares stored `content` to the new chunk | compares the text row to the new chunk |

The third is the chunk-reuse optimisation — how a re-index skips embedding a
chunk whose text has not changed. It must move with the others or every
re-index re-embeds everything and the `reused` counter goes to zero.

### When a row is missing

A match comes back from Pinecone with no corresponding text row. Causes:
backfill incomplete, `index.db` restored from an older backup, or a partial
write.

**Policy: drop the chunk from the result, log an error, keep streaming.** This
follows the project's standing rule — a non-essential stage failing emits an
error and does not kill the request. An answer grounded in four chunks instead
of five is degraded; a failed request is worse. The log line must name the
vector id, because a run of these means the two stores have diverged and
reconciliation is due.

Never substitute empty text silently. A chunk with no content reaching the
prompt is a citation the model will invent around.

---

## 7. Migration phases

Each phase is a separate deploy. No phase requires the next one.

### Phase A — dual-write

Write chunk text to **both** Pinecone metadata and `index.db`. Nothing reads
the new table yet.

- New `services/index_db.py` (connection, schema) and `services/chunk_store.py`
  (read, write, delete).
- `ingestion.index_source` writes text rows before the upsert.
- `index_catalog.delete_document`, `prune_vectors` and `prune_chunks_beyond`
  delete the matching rows.

*Reversible by:* deploying the previous build. The new table is simply ignored.

### Phase B — backfill

A one-off command that walks the index and copies `content` out of Pinecone
metadata into `index.db` for every vector without a row.

- Idempotent and resumable — it skips rows that already exist, so it can be run
  repeatedly and interrupted safely.
- Reports how many vectors it saw, wrote, and skipped.
- **Exit criterion: every vector id in Pinecone has a text row.** Verified by
  the reconcile command in §8, not by the backfill's own optimism.

*Reversible by:* nothing to reverse. It only adds rows.

### Phase C — flip reads, keep the fallback

Point the three readers at `index.db`, **falling back to Pinecone metadata when
a row is missing**, and log every fallback at WARNING with the vector id.

This is the phase that proves the migration. Run it until the fallback log is
silent under real traffic. A fallback that fires means Phase B missed something,
and the fallback means users never see it.

*Reversible by:* deploying the previous build. Pinecone still holds every value.

### Phase D — stop writing content to Pinecone

Only once Phase C has been quiet for long enough to trust.

- Remove `content` from `build_metadata`.
- Remove the fallback and its dead code path.
- Existing vectors keep their now-unread `content` until re-indexed. That is
  wasted storage, not a fault.
- **Optional:** a strip command that re-upserts existing vectors without
  `content` to reclaim the storage immediately. Otherwise it decays naturally
  as files are re-indexed.

*Reversible by:* this is the point of no easy return. Rolling back after
Phase D means re-indexing, because vectors written under D have no text in
Pinecone to fall back to. Do not enter Phase D on the same day as Phase C.

---

## 8. Reconciliation

A command, and later a scheduled job, answering: **do the two stores agree?**

```
vector ids in Pinecone  ⟷  vector_id rows in index.db
```

| Discrepancy | Meaning | Fix |
|---|---|---|
| Vector with no text row | a query would drop this chunk | backfill it, or re-index the file |
| Text row with no vector | leftover from a failed write or delete | delete the row |

Reporting-only by default; `--fix` to act. This is the same shape as the
existing cache freshness check — it exists because the two stores can drift and
something has to notice.

---

## 9. Verification

Before Phase C is considered done:

- **Round trip.** Index a file, confirm chunk count and text match byte-for-byte
  between `GET /sources/{key}` and the file's actual chunks.
- **Reuse still works.** Re-index an unchanged file; `reused` must equal the
  chunk count and `embedded` must be zero. This is the check most likely to
  catch a mistake in `index_plan._matches`.
- **Partial re-index.** Change one paragraph, re-index, confirm only the
  affected chunks re-embed and the rest are reused.
- **Shrink.** Re-index a file that produces fewer chunks; confirm the leftover
  vectors *and* their text rows are pruned.
- **Delete and deindex.** Both remove rows from both stores.
- **Missing row.** Delete a text row by hand, ask a question that would retrieve
  it, and confirm the chunk is dropped, the error is logged, and the answer
  still streams.
- **Reconcile** reports zero discrepancies on a clean index.
- **A real `POST /chat`** returns citations with correct text.

---

## 10. Risks and limits

- **This makes the application stateful.** Today two instances can share one
  Pinecone index and both work. Afterwards, chunk text lives on one machine's
  disk. **A second instance would need Postgres, not SQLite.** The schema ports
  directly, but plan for it before scaling out — this is the single biggest
  consequence of the change.
- **`index.db` becomes backup-critical.** Losing it means re-embedding every
  file. R2 still has the originals, so it is a cost event rather than data loss,
  but it is a cost that did not exist before.
- **Phase D is one-way** without re-indexing.
- **A third copy of the text exists during Phases A–C** (R2, Pinecone, SQLite).
  That is the price of a reversible migration.
- **Retrieval gains nothing in latency.** If someone expects chat to get
  faster, correct that expectation early.

---

## 11. Files

**New**

| File | Holds |
|---|---|
| `app/services/index_db.py` | Connection and schema for `index.db`. Mirrors `prompt_db.py`. |
| `app/services/chunk_store.py` | Read, write and delete chunk text. Framework-agnostic. |
| `app/services/index_reconcile.py` | The Pinecone ⟷ SQLite comparison, report and fix. |

**Changed**

| File | Change |
|---|---|
| `app/services/ingestion.py` | Writes text rows before the upsert. |
| `app/services/provenance.py` | Phase D: `content` leaves `build_metadata`. |
| `app/services/retrieval.py` | Content by lookup; drop-and-log when a row is missing. |
| `app/services/index_catalog.py` | `_to_chunks` reads rows; deletes and prunes remove them. |
| `app/services/index_plan.py` | `_matches` compares against the text row. |
| `app/services/deletion.py` | Deletes rows alongside vectors. |
| `app/config.py` | Adds `index_store_path`. |
| `app/main.py` | Opens `index.db` at startup. |
| `app/services/source_cache.py` | Phase D: the detail entry can be dropped — its data is now local. |

Unaffected, worth confirming: `trace_store` and `evaluation_export` already keep
their **own copies** of chunk text by design, so a trace stays judgeable after
the chunk it cites has been re-indexed. Nothing there changes.

---

## 12. Sequencing

| Phase | Shape of the work | Gate before proceeding |
|---|---|---|
| A — dual-write | new db, store, write paths | round-trip test passes |
| B — backfill | one command | reconcile reports zero missing rows |
| C — flip reads | three readers, fallback, logging | fallback log silent under real traffic |
| D — stop writing | remove `content`, remove fallback | C has been quiet for days, not hours |

**Recommendation on scope:** do the document-grain `indexed_document` table in
Phase A as well. It is the same database, the same write paths, and the same
deletion hooks — the marginal work is small, and it is what removes the 1204ms
Pinecone probe from `GET /sources`. Doing it separately means touching
`ingestion.py` and `deletion.py` twice for the same reason.
