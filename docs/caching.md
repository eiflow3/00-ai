# Caching

`GET /sources` was the slowest screen in the app, and it was slow for a
structural reason rather than a fixable one. Listing files meant asking two
services what they held, and the vector index has no cheap way to answer
"which documents do you have?" — it only knows vectors. Finding out meant
walking every vector id in the index, then reading metadata back for each
file. Every page load. Measured against the live index: **2.4 seconds inside a
4.8 second request**, before a single file had been indexed.

Slow was only half the problem. Those reads are billed per operation, so the
old listing spent **two Pinecone calls per indexed file on every page load** —
and the bill scaled with how often anyone refreshed the screen.

This document is the whole caching design: what is cached, what deliberately
is not, how a cached answer is proven still true, and the rule that decides
which side of that line a new endpoint falls on.

The short version: **only two endpoints are cached, only half of each is
cached, and the half that is cached is the half that leaves the machine** —
which is also the only half anyone charges us for.

---

## 1. The rule that decides everything

A cache only helps if **the cache is faster than the thing it replaces**. That
sounds obvious and is routinely got wrong, because "add Redis" is treated as a
synonym for "make it faster". It is not. Redis is a network round trip. Putting
it in front of something faster than a network round trip makes that thing
slower.

Every store this app reads sits somewhere on a latency ladder. Measured on this
deployment:

| Tier | Example here | Per read |
|---|---|---|
| In-process memory | a Python dict | ~0.000 ms |
| Local file / SQLite | `prompts.db`, `runs.db`, `traces.db` | ~0.057 ms |
| Redis | the configured cache | ~0.103 ms |
| Another service over the internet | Cloudflare R2, Pinecone | 100–3000 ms |

**Cache upward on that ladder, never sideways or down.** Redis in front of
Pinecone is a ~1000x win. Redis in front of SQLite is a 1.8x *loss*. The gap
between the tier you are reading from and the tier you would cache into is the
entire justification, and if it is small or negative there is no case to make.

That single rule explains every decision below, including the endpoints that
were deliberately left alone.

### The second reason: reads from a third party are billed

Latency is only half the case. R2 and Pinecone charge **per operation**, so an
uncached read is not merely slow — it is an invoice line. A local SQLite read is
free and fast; a third-party API read is billed and slow. The two axes point the
same way, which is why one rule covers both.

The number that matters is **billed operations per request**, and it is exact
rather than estimated. For a corpus of *N* indexed files:

| `GET /sources` | R2 calls | Pinecone calls |
|---|---|---|
| Before this work | 1 | **2N + 2** |
| Cold miss (rebuild) | 1 | 3 |
| **Warm hit** | 1 | **1** |

**A warm hit is not zero Pinecone calls.** The freshness probe runs on *every*
request, cached or not — the cache removes the walk, not the probe.

That probe used to be two calls: `has_index()` against the control plane, then
`describe_index_stats()` against the data plane. The guard was redundant —
`describe_index_stats()` raises `NotFoundException` for a missing index, which
is exactly what `has_index()` was being asked, from a call that had to happen
anyway. Removing it, and caching the index handle rather than rebuilding it per
call, took the probe from **1204ms to 274ms** and a warm request from 1.18s to
0.25s.

The `2N` was the killer: the old listing asked the index about every file
individually — one prefix listing and one metadata fetch each. At 50 indexed
files that is **102 billed Pinecone calls on every page load**. Note that the
cold path improved too, independently of caching: describing the whole index in
one listing plus one batched fetch removed the per-file round trip whether the
answer is cached or not.

| `GET /sources/{key}` | R2 calls | Pinecone calls |
|---|---|---|
| Before this work | 1 | **4** |
| Cold miss (rebuild) | 1 | 2 |
| **Warm hit** | 1 | **1** |

The detail endpoint's probe *is* its one call — listing the file's vector ids,
which was needed anyway. It has no separate probe on top.

The old detail read listed the same file's vector ids *twice* and fetched from
it twice, because the status and the chunks were built independently.

### Cost stops scaling with traffic

The more important property is not the ratio but what it is a ratio *of*.
Uncached, spend scales with **page loads** — every refresh, every open tab, every
poll re-pays the full amount, and two people watching the same screen pay twice.
Cached, spend is bounded at roughly **one rebuild per TTL window** no matter how
many clients are watching, because they all collapse onto the same entry.

Three honest deductions from that:

- **The probe is not free.** A warm `GET /sources` still spends one Pinecone
  call and one R2 listing. The trade is `2N + 2` calls for `2`, not for zero.
- **Not every call bills the same way.** The operations the cache actually
  eliminates — `list` and `fetch` — are data-plane reads that consume read
  units. The surviving probe is a metadata/control-plane call, which is priced
  differently or not at all. Confirm against Pinecone's current billing before
  reasoning about the invoice; the counts above are what this codebase does, not
  what it is charged for.
- **`?refresh=true` and indexing runs pay full price**, by design. Both bypass
  the cache. `refresh` is a button a person presses; a run bypasses because a
  cached verdict must not decide what gets re-embedded.
- **This is not where the money goes.** Embedding and generation dominate the
  bill; storage reads are the smaller line. Caching them is worth doing because
  it is nearly free to do, not because it is the biggest lever available.

Exact per-operation prices are deliberately not quoted here — they change, and a
number in a repo doc goes stale silently. Check Cloudflare R2's Class A/B
operation pricing and Pinecone's read-unit pricing for current figures. The
operation *counts* above are properties of this codebase and will not drift
without a code change.

---

## 2. What is cached, and what is not

| Endpoint | Reads from | Cached? | Why |
|---|---|---|---|
| `GET /sources` | R2 **and** Pinecone | **Yes — the Pinecone half only** | The index walk is seconds. R2's listing is one call and stays live. |
| `GET /sources/{key}` | R2 **and** Pinecone | **Yes — the Pinecone half only** | Fetching every chunk's text is the cost. The HEAD stays live. |
| `GET /prompts` | local SQLite | In-process memo, **not Redis** | Redis would be slower than the file. See §10. |
| `GET /sources/index/runs` | memory + local SQLite | No | Already in-process. Nothing to gain. |
| `GET /sources/index/runs/{id}/events` | memory | No | A live SSE stream. Caching it would defeat its purpose. |
| `GET /chat/models` | config in memory | No | Already in-process. |
| `GET /traces`, `GET /evaluations` | local SQLite | No | Local file reads, and heavily filtered — the cache would miss constantly. |
| `POST /chat` | Pinecone + an LLM | No | Answers are non-deterministic and streamed. A cached answer is a wrong answer. |
| Every `POST` / `PUT` / `DELETE` | — | No | Writes are not cacheable. They *invalidate* (§5). |

Two endpoints. Everything else was measured or reasoned about and left alone.

Note what the cached rows have in common: they are the **only** reads in the
app that leave the machine, and therefore the only ones that are both slow and
billed. Every uncached row above reads from local disk or memory — free, fast,
and nothing a cache could improve.

---

## 3. The two-halves design

Both cached endpoints join two independent stores, and the halves are not
equally expensive:

```
GET /sources
├── Cloudflare R2  ── list the bucket ──────────── 1 call     ← LIVE, never cached
└── Pinecone       ── walk every vector id,
                       then read metadata back ─── seconds    ← CACHED
```

Only the expensive half is cached. This is the single most important property
of the design, and it is what makes the whole external-mutation problem
tractable:

**A file added or deleted straight from the R2 console appears on the very next
request.** Not after a TTL — immediately. There is nothing to invalidate,
because the bucket is read live every single time.

That leaves exactly one store whose changes could go unnoticed, which narrows
the problem enough to solve properly (§6).

Keeping R2 live is affordable precisely because it is one operation. Pinecone
charged per file; R2 charges per listing. Caching the cheap half would have
bought almost nothing and cost the immediacy that makes console edits visible.

---

## 4. What actually happens on each request

### `GET /sources`

**On a hit** — two live calls, no index walk:

1. List the R2 bucket (live).
2. Ask Pinecone its **total vector count** — one cheap call.
3. Read the generation counter from the cache.
4. If the count and the counter both match what the cached entry was built
   from, use the cached index data.
5. Join the two halves, stamp the live `indexing` / `queued` flags, return.

**On a miss** — step 4 fails, so the index is walked and the result re-cached.

### `GET /sources/{key}`

Same shape, different freshness probe:

1. HEAD the object in R2 (live).
2. List **this file's vector ids** — one prefix call, cheap.
3. Read this file's version counter.
4. If the id list and the counter both match the cached entry, use the cached
   chunks — skipping the fetch of every chunk's text, which is the real cost.

Listing the ids is not overhead. It was already needed to know what to fetch;
it now doubles as the freshness proof.

### One thing that is never cached

Each row carries `indexing` and `queued` — whether a run is embedding this file
*right now*. Those come from in-process state and are **re-stamped on every
read**, cached or not. A cached row never reports stale progress.

---

## 5. Invalidation: three mechanisms

A cached entry is discarded when any one of these fires. They are listed in the
order they are checked, which is also cheapest-first.

### 1. A generation counter — instant, covers our own writes

Every write bumps a counter, and every cached entry records the counter value it
was built under. A mismatch is a miss.

Bumped by: `upload`, `replace`, `delete`, `deindex`, and **each file as it
finishes** in an indexing run — per file rather than per run, so a client
watching the progress stream sees its list update as the run proceeds.

Bumping a counter rather than deleting entries keeps invalidation at two round
trips no matter how many entries a write affects.

This exists because the freshness check below is cheap but *lagging*: Pinecone's
own statistics take seconds to reflect a write, so immediately after an upload
the index would still report the old count and a cache built on that alone
would confidently serve pre-write data.

### 2. A freshness check — catches changes made outside the app

Each entry records what the index looked like when it was built, re-checked on
every read:

| Endpoint | Recorded | Detects |
|---|---|---|
| `GET /sources` | total vector count | any vector added or removed anywhere in the index |
| `GET /sources/{key}` | that file's exact vector ids | any chunk added or removed for that file |

This is what catches **someone working directly in the Pinecone console**.

### 3. A TTL — the backstop

60 seconds by default. It covers the one case neither mechanism above can see:
an edit that leaves the vector count *and* the id set unchanged — rewriting one
chunk's text in place from the console. Nothing observable moves, so only the
clock catches it.

### And an explicit escape hatch

`?refresh=true` on either endpoint bypasses the cache and rebuilds it. This is
what to use after changing something on a provider console when you do not want
to wait out the TTL, and to confirm what is *really* stored rather than what was
last read.

Indexing runs always bypass the cache. A cached verdict must never decide what
gets re-embedded — that decision spends money.

---

## 6. The external-mutation problem, stated honestly

The original question this design had to answer: *what happens when someone adds
or deletes data directly in the R2 or Pinecone console, where the app has no way
of knowing?*

| What changed, and where | Detected | How |
|---|---|---|
| File added in the R2 console | **Immediately** | R2 is never cached |
| File deleted in the R2 console | **Immediately** | Same — it becomes an `orphaned` row |
| Vectors added in the Pinecone console | Next request | Total vector count moved |
| Vectors deleted in the Pinecone console | Next request | Same |
| Chunks added/removed for one file | Next request | That file's vector id set changed |
| **A chunk's text rewritten in place** | **Up to 60s** | Nothing observable moves — TTL only |

Only the last row is a genuine window, and it requires someone to edit vector
metadata by hand without changing the vector count. Working directly on either
console is outside the contract these endpoints keep — the app is the only
writer that can invalidate instantly. `?refresh=true` closes the window on
demand.

**What was deliberately not built:** R2 event notifications through a Cloudflare
Queue and Worker into an authenticated invalidate endpoint. It would make R2
changes instant — but R2 changes are *already* instant here, because R2 is not
cached. It would have added public ingress and a shared secret to solve a
problem the two-halves design had already removed, and Pinecone has no
equivalent webhook, so it would not have covered the half that actually needs
it.

---

## 7. The cache backend

`services/cache.py` is deliberately generic: it stores strings, counters and
expiry, and knows nothing about sources or vectors. What is worth caching lives
in `services/source_cache.py`.

**Redis when configured, an in-process dict otherwise.** The fallback is not
only for a missing URL — a cache is an optimisation, so a Redis that disappears
mid-flight must degrade rather than take the endpoints down with it:

- Every Redis call is bounded by a **2 second timeout**. A cache slower than the
  work it replaces is worse than no cache.
- A failed call drops to the in-process backend and schedules a **30 second**
  re-probe. A Redis that comes back is picked up on its own.
- **Nothing in this module raises.** A cache that cannot answer reports a miss,
  and the caller does the work it would have done anyway.

The in-process backend expires on read rather than sweeping on a timer — no
background task to leak — and is bounded at **2048 entries**.

A failed counter bump returns `0`, which matches no stored generation. A cache
that cannot be written therefore *invalidates* rather than serving stale data.

---

## 8. Configuration

| Setting | Default | Does |
|---|---|---|
| `APP_CACHE_ENABLED` | `true` | Off reads through to the stores every time. |
| `APP_CACHE_TTL_SECONDS` | `60` | Backstop expiry on cached payloads. |
| `REDIS_URL` | *(empty)* | Whole connection string. Wins over the parts below. |
| `REDIS_HOST` | *(empty)* | Empty (with no URL) means in-process cache. |
| `REDIS_PORT` | `6379` | |
| `REDIS_USERNAME` | *(empty)* | For Redis 6 ACLs. |
| `REDIS_PASSWORD` | *(empty)* | |
| `REDIS_DB` | `0` | |
| `REDIS_TLS` | `false` | `true` selects `rediss://`. |
| `APP_PROMPT_CACHE_ENABLED` | `true` | The prompt memo (§10). **Turn off for multi-worker.** |

The split host/port/password form exists because it needs **no
percent-encoding** — a generated password containing `@`, `:` or `/` breaks a
URL and is fine here. Credentials are quoted internally when the DSN is built.

### Running without Redis

Everything works. The cache runs in-process, which is correct for a single
worker but is lost on every reload. Set Redis once there is more than one
worker, or once losing the cache on restart costs more than the round trips it
saves.

---

## 9. Cache keys

All keys live under `sources:v2:`. The version is bumped by hand when the stored
shape changes, so a deploy cannot read an old entry into new models.

| Key | Holds | TTL |
|---|---|---|
| `sources:v2:epoch` | global generation counter | 7 days |
| `sources:v2:documents` | every indexed file's index record | 60s |
| `sources:v2:version:{document_id}` | one file's generation counter | 7 days |
| `sources:v2:detail:{document_id}` | one file's chunks | 60s |

`{document_id}` is the first 16 hex characters of `sha1(source_key)` — the same
id that prefixes the file's vector ids. Object keys carry slashes, spaces and
unicode; the derived id is short and safe in any backend.

Counters outlive the entries they guard by design. A counter that expired and
reset to `1` could otherwise match a payload built under an earlier generation
of the same number.

### Observability

Every read logs at INFO, with the **reason** for a miss:

```
sources listing: cache hit (redis), 42 indexed document(s)
sources listing: cache miss (redis) — invalidated by a write; walked the index for 42 document(s) in 380 ms
sources listing: cache miss (redis) — index vector count moved, 128 -> 131; walked ...
sources detail docs/b.md: cache hit (redis), 12 chunk(s)
sources detail docs/b.md: cache miss (redis) — vector ids changed, 12 -> 9; fetched 9 chunk(s) in 90 ms
```

The reason is the part worth watching. `invalidated by a write` is the app
working normally. **`vector count moved` or `vector ids changed` means something
edited Pinecone from outside the application** — that line is the detector for
console editing.

---

## 10. The counter-example: why prompts are not in Redis

Worth reading before caching anything else here, because the intuition that
"Redis makes things faster" fails on a real case in this codebase.

`GET /prompts` reads four rows from a local SQLite file on every chat request.
An obvious candidate for the cache that was already built. Measured against this
deployment's Redis:

| | Per read |
|---|---|
| Reading the four rows from SQLite | 0.057 ms |
| A Redis round trip | 0.103 ms — **1.8x slower** |
| An in-process memo | ~0 ms — **342x cheaper than the read** |

Redis is a network call *even on the same machine*: out of the process, through
a socket, into another program, back. SQLite is a library compiled into the
process — a function call and a file read the OS already has in RAM.

So prompts **are** cached, one tier up: an in-process memo, cleared by `save`
and `reset`. That preserves the feature's guarantee exactly — an edit applies to
the next question, with no TTL and no staleness window at all.

What makes that safe is the **writer count**, and it is the mirror image of the
source cache. The source cache must assume someone is editing R2 or Pinecone
from a console, so it re-checks freshness on every read. Nothing edits
`prompts.db` but `save` and `reset`, so clearing the memo there is *complete* —
there is no path by which it can go stale.

That reasoning is single-process. A second worker would hold its own memo and
never hear about the first one's write, which is what
`APP_PROMPT_CACHE_ENABLED=false` is for.

---

## 11. Adding caching to a new endpoint

Work through this in order. Most endpoints stop at the first question.

1. **What tier does it read from?** (§1) If it is already in-process or a local
   file, stop — unless an in-process memo is available, as with prompts.
2. **Does anyone bill us for the read?** A third-party API is charged per
   operation, which is a reason to cache even where the latency alone would not
   justify it — and a reason to count operations, not just milliseconds.
3. **Measure it.** Not "it feels slow". The prompts decision reversed on a
   measurement that took two minutes to run.
4. **Split it.** Is only part of the read expensive? Cache that part and leave
   the cheap half live. This is what removes most of the invalidation problem
   before you have to solve it.
5. **Who else can write to the store?** If only this app, a counter cleared on
   write is complete and you need no TTL. If a console or another service can
   write, you need a cheap freshness probe *and* a TTL for what the probe
   cannot see.
6. **Find a cheap freshness probe.** Something orders of magnitude cheaper than
   the read, that moves when the read's answer would. Total counts, id sets,
   etags, `updated_at` maxima.
7. **Is any of it live state?** Progress flags, "who is running this now" —
   re-stamp those after the cached read, never store them.
8. **Invalidate at every write path**, and log hit/miss with the reason.

---

## 12. Limits

- **The freshness probe costs a round trip, and an operation.** A warm
  `GET /sources` spends ~250ms across two calls — Pinecone's stats probe at
  ~274ms dominates it, and R2's listing is ~60ms of it. The cache removes the
  index walk, not the live half; that is the deliberate trade for R2 changes
  being visible immediately. Removing the probe entirely is what the
  `indexed_document` projection would buy.
- **A short TTL trades money for freshness.** Sixty seconds bounds how long an
  in-place console edit stays invisible, and also how often the index is walked
  under sustained traffic. Raising it saves operations and widens that window;
  lowering it does the reverse. It is the one knob where the two goals of this
  design actually pull against each other.
- **A chunk rewritten in place is invisible for up to the TTL.** §6.
- **`sources:v2:documents` is one entry for the whole index.** Any write to any
  file rebuilds all of it. Correct, and cheap at this corpus size; a corpus in
  the tens of thousands would want per-file entries.
- **Pinecone's statistics lag.** Which is why the generation counter exists and
  why the probe alone is not trusted.
- **`prompt_cache_enabled` must be turned off for multi-worker.** There is no
  cheap fix — a cross-worker check costs more than the read it replaces.
- **The in-process fallback is per-process.** Two workers hold two caches. They
  cannot serve *stale* data — the freshness checks still run — but they duplicate
  the work. Redis is the fix.

---

## 13. Files

**New**

| File | Holds |
|---|---|
| `app/services/cache.py` | The backend. Redis with an in-process fallback, degradation, counters. Knows nothing about sources. |
| `app/services/source_cache.py` | What is cached and when it stops being true. Key building, freshness rules, miss reasons, invalidation. |

**Changed**

| File | Change |
|---|---|
| `app/services/sync_status.py` | Reads the index side through the cache; takes `refresh`. Storage stays live. |
| `app/services/index_catalog.py` | Adds `list_indexed_documents` (whole index in one listing plus one batched fetch) and `read_document` (one file's record and chunks from a single fetch). Removes the round-trip-per-file, so the *cold* path is cheaper too. |
| `app/services/uploads.py`, `deletion.py`, `index_queue.py` | Invalidate on every write. |
| `app/services/prompt_store.py` | The in-process memo, cleared by `save` and `reset`. |
| `app/routers/sources.py` | The `refresh` query parameter on both reads. |
| `app/docs/sources.py` | Freshness prose on both endpoints' OpenAPI descriptions. |
| `app/config.py` | Cache and Redis settings; `redis_dsn` resolves URL-or-parts. |
| `app/main.py` | Closes the Redis connection on shutdown. |

---

## 14. Verified

Against the live R2 bucket, Pinecone index and Redis:

- `GET /sources` — **4.07s cold → 0.245s warm**, byte-identical payloads.
  (Before the probe was trimmed: 4.81s cold, 1.18s warm.)
- `GET /sources/{key}` — **0.96s cold → 0.29s warm**.
- The log showed the full cycle: `nothing cached` → `hit` → `refresh requested`
  → `hit` → `invalidated by a write`.
- A no-op deindex (zero vectors removed) still invalidated correctly.
- Redis held exactly the expected keys with the expected TTLs — three, not
  four: a file with no vectors gets no detail entry, because caching "nothing
  indexed" would make an index that lags a write report the file as empty for a
  whole TTL.

Behavioural checks, run both against Redis and against the in-process fallback:

- **29 checks** on the cache — cold/warm, R2 adds and deletes seen immediately,
  Pinecone deletes detected via the vector count, detail chunk sets following an
  external change, `refresh=true`, `cache_enabled=false`, TTL expiry, a failing
  stats probe still answering, and an unreachable Redis degrading to memory.
- **10 checks** on the prompt memo — 50 reads hitting the table zero times, a
  save and a reset each visible on the very next read, and
  `prompt_cache_enabled=false` reading through every time.

Numbers above were taken with an **empty index**, so they are the floor. The
saving grows with the number of indexed files, because the eliminated work was
per-file.
