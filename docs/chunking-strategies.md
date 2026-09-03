# Chunking strategies

One document can be cut into embeddable segments several ways, and which way
you chose changes what retrieval can find. This document is the whole of that
feature: the four strategies, what a *variant* is, where a variant's vectors
live, how the comparison is scored, and what it costs.

The short version: **a variant is a strategy plus its geometry, its vectors live
in a namespace of their own, which variant is better is decided by putting a
golden set to all of them and counting — and the winner becomes the app's
default answer by pointing production at it, with nothing re-embedded.**

The mechanics of the default cut — token measurement, the boundary search, the
offset arithmetic — are in [chunking.md](chunking.md). This document is about
having more than one of them.

---

## 1. Why this exists

The pipeline had exactly one way of cutting a document, and no way to find out
whether it was the right one. That is not a gap in a feature; it is the
question the application is for.

Two things were in the way, and both had to go:

- **Re-indexing overwrites in place.** A vector id names a slot, so cutting the
  same file a second way replaced the first way rather than sitting beside it.
  There was nothing to compare against.
- **Chunking was not addressable.** The geometry was a request field and the
  algorithm was not a choice at all, so "answer this question from the other
  chunking" could not be expressed.

---

## 2. A variant

A **variant** is a strategy plus the geometry it ran at:

```
recursive-512-64        strategy · chunk size · overlap
```

Geometry is part of the identity rather than a setting beside it. The same
splitter at 512 tokens and at 256 retrieves differently enough to be a separate
experiment, and an experiment that quietly shares vectors with another is not
an experiment.

`services/chunk_variants` owns every rule connecting a variant to storage, the
way `services/provenance` owns the rules connecting a source file to its vector
ids. Nothing else spells out a namespace, and nothing else knows which index
the experiments are in.

The id is readable on purpose rather than hashed. It is the namespace in the
Pinecone console, it appears in log lines, and `parse` reads it back into the
configuration that produced it without consulting anything.

| Function | Answers |
|---|---|
| `variant_id(config)` | What is this configuration called? |
| `parse(id)` | What configuration does this name? |
| `label_for(config)` | What does a person call it? — `recursive · 512/64` |
| `space_for(id)` | Where do its vectors live? |
| `resolve(variant, fallback)` | A request named a variant *and* a geometry — which wins? |

`resolve` is where that last precedence is settled, once: **a named variant wins
outright**, and the request's own `strategy`, `chunk_size` and `chunk_overlap`
are ignored. Honouring a conflicting size would produce vectors whose name lies
about them.

---

## 3. Where a variant's vectors live

In its own **namespace**, one per variant.

```
rag-chunk-lab
  ├── boundary-512-64         one namespace per variant
  ├── fixed-512-64
  ├── recursive-512-64   ←    production points here
  └── structural-512-64
```

The property that matters: **a query cannot cross a namespace.** The isolation
is the vector store's, not a metadata filter every call site has to remember to
apply, so comparing two variants is comparing two closed sets of vectors — and
indexing one can never disturb another.

There used to be a second index, `rag-index`, holding what the app answered
from. It has been retired; see §3.2.

### 3.1 Why not one index per strategy

Pinecone's Starter plan allows five indexes, which is a hard ceiling at four
strategies plus production. Chunk size and overlap are variables worth sweeping
alongside the strategy — `recursive@512/64` against `recursive@256/32` is a real
experiment — and there is no room for them under a cap of five. Namespaces have
no such limit.

The mapping lives in `space_for` alone, so pinning one variant to an index of
its own is a change there and nowhere else.

### 3.2 Production is a pointer, not a place

Production used to be its own index, written only from the Sources screen. That
made "which way of cutting these documents answers best" a question you could
measure but not act on: adopting the winner meant re-embedding the corpus into
the production index and trusting the copy.

So production is now a **stored variant id** naming the namespace that answers
when a request names none itself. `services/answer_space.py` owns it, in a
database of its own — it is configuration, like an edited prompt, and a pointer
that reset on restart would move every subsequent answer with nothing recording
that it had.

Moving it is instantaneous and reversible, because the vectors already exist:
they were written by the comparison run that proved they were better.

Three rules keep it safe to move:

- **Only at something that can answer.** An empty namespace, a half-embedded
  one, or an id no strategy can reproduce is refused at the moment of pointing
  — the alternative is discovering it in an ungrounded answer an hour later.
- **Never corrected silently.** A namespace emptied afterwards leaves the
  pointer where it is and reports `missing`. Falling back to somewhere else
  would answer from a different corpus than the screen names.
- **Reads never provision.** Every read path uses the probing index handle, so
  asking about an index that no longer exists comes back empty instead of
  bringing it back into existence.

What this trades away is the old blast-radius argument: a lab bug could not
write into an index it never opened, and production is now one namespace among
the rest. What protects it instead is that **writes must name their target** —
`space_for` refuses an id it cannot parse rather than creating a namespace for
it, and no screen writes to production implicitly.

Only four things genuinely require a separate index — a different embedding
width, a different metric, a different region, or a tenant large enough to need
its own read capacity — and none of them describe a chunking strategy. Metadata
shape is not among them, which is worth stating because it looks like it should
be: metadata is per record, so two namespaces can carry entirely different keys.
[questions/06-pinecone-index-isolation.md](../questions/06-pinecone-index-isolation.md)
has the whole model, including the multitenancy case this pattern generalises
to.

### 3.2 The vector space is an argument

`services/vector_store` takes a `VectorSpace` — an index name and a namespace —
on every read and write. Both fields default to empty, meaning the configured
production index and the default namespace, so a caller that does not care
keeps not caring and the pipeline behaves exactly as it did before namespaces
existed.

The space is threaded through `index_catalog`, `index_plan`, `ingestion`,
`retrieval` and `deletion`. `sync_status` is deliberately *not* threaded: the
Sources screen reports production, and a variant is not a state of a file.

---

## 4. The four strategies

One module each under `services/chunking`, named in `registry.py`, described for
a person in `catalog.py`. All four are pure: same text and geometry in, same
segments out, no I/O and no clock.

| Strategy | What it does | Where it wins |
|---|---|---|
| `boundary` | A token window, trimmed back to the last blank line or sentence end in its final quarter. | The pipeline's original behaviour, and the baseline. Reliable on prose. |
| `fixed` | Equal token windows, cut wherever the count runs out. | Nothing, usually — it is the floor every other strategy has to beat, and until you run it that claim is untested. Its one real advantage is uniform size, so no match is flattered by being longer. |
| `recursive` | Splits on blank lines, then lines, then sentences, then spaces, going finer only where a piece is still too long, then packs the pieces back up to the budget. | Documents whose paragraphs are longer than `boundary`'s search window, where `boundary` gives up and cuts mid-sentence. |
| `structural` | One section to a chunk, using the document's own headings, with the heading repeated at the top of every chunk. | A genuinely sectioned document — a report, a contract, a policy — where a section is already sized by the argument being made. |

### 4.1 What `structural` has to get right

Left naive, a section-per-chunk strategy is judged on chunk size rather than on
method, so two corrections are load-bearing:

- **Sections over the budget are split inside themselves**, at natural breaks,
  so no chunk exceeds the limit the other three are held to.
- **Sections under a quarter of the budget are merged forward** into the next
  one. A three-line preamble embedded alone is a vector that matches everything
  weakly and nothing well. A merged span covers the source text between its
  first and last section, so the headings inside it survive verbatim rather than
  being dropped or re-inserted.

The heading itself rides on every chunk of the section. A retrieved passage
saying "revenue grew 19.8 percent" is ambiguous; the same passage under
"4.2 Cold Chain" is not, and the heading costs a dozen tokens. It is skipped
when the text already opens with it, which is what stops the document's own
title block being printed twice.

Heading detection is not this module's. `services/document_sections` already
reads ruled headings, ATX headings and numbered or capitalised lines, because
the golden-set generator needed exactly the same thing. A second implementation
would be the same job done twice, and the two could disagree about the same
document.

### 4.2 On the Meridian report, at 512/64

| Strategy | Chunks | Smallest | Median | Largest | Repeated |
|---|---|---|---|---|---|
| `boundary` | 8 | 303 | 460 | 506 | 13% |
| `fixed` | 7 | 400 | 511 | 512 | 11% |
| `recursive` | 8 | 171 | 457 | 502 | 9% |
| `structural` | 11 | 130 | 254 | 473 | 4% |

Worth reading twice: `structural` produces half-sized chunks on this document,
because its sections are short. That is not a bug to tune away — it is the
strategy being honest about the document, and it is exactly the sort of thing a
free preview is for.

---

## 5. Adding a strategy

Three touches, and the code refuses to start if you make only two.

1. A member on `ChunkStrategy` in `schemas/chunking.py`.
2. A module under `services/chunking` exposing
   `async def split(text, config, context) -> list[Segment]`.
3. A line in `registry._STRATEGIES`.

The registry checks at import that every enum member has an implementation and
raises if one does not: a strategy offered through the API with nothing behind
it would be a 500 the first time somebody picked it, and finding that out when
the process starts is better than finding it out from a user.

A description in `catalog._SPECS` is optional but expected — it is what the
picker shows, and the catalog is driven by the registry, so a strategy cannot be
implemented and then quietly left out of the list.

Every strategy is `async` whether or not it awaits anything, because cutting by
embedding distance or by asking a model for a summary is a real strategy people
will want next, and a synchronous protocol would have to be rewritten — along
with every call site — the first time one arrives. `StrategyContext` carries the
embedding model for the same reason.

---

## 6. Preview

`POST /chunking/preview` reads a file, runs a strategy over it and returns every
chunk with the shape of the cut. **Nothing is embedded, nothing is written, and
no vector is touched.**

This is the step that makes the feature affordable to use. Choosing a strategy
by indexing it costs money and a minute; choosing it by preview costs neither,
and only the ones worth trying get embedded.

The summary matters more than the chunk list:

| Field | What it tells you |
|---|---|
| `chunk_count` | How many vectors indexing this would create. |
| `min` / `median` / `max_tokens` | How even the cut is. |
| `total_tokens` | What embedding it would measure. |
| `repeated_fraction` | How much of that is overlap — text embedded twice. |

`variant_id` names the variant this configuration *would* create, so the client
can say exactly what pressing Index will produce before it is pressed.

Each segment also carries a `note` — the heading it sits under, or that no
break was in reach. Diagnostic only: it is never embedded and never stored.

---

## 7. Indexing a variant

The same endpoint, queue, run history and event stream as any other indexing
run. `POST /sources/index` gains one field:

```json
{ "keys": ["01-meridian-fy2025-annual-report.txt"], "variant": "recursive-512-64" }
```

### 7.1 The queue entry carries the terms

Chunk geometry used to be fixed on the run when it started, and files added
later joined on those terms. That was correct when every run cut the same way
and is wrong now: queuing one file under four strategies would have embedded
three of them the first one's way and produced four copies of the same thing.

So strategy, geometry, destination and `force` moved onto the queued entry.
`services/index_registry.QueuedFile` holds them, and `_process` reads them per
file. Consequences worth knowing:

- **Four clicks make one run.** Queuing the same file under four variants is
  four entries behind one progress bar, embedded one after another.
- **Deduplication is per file *and* variant.** Re-queuing the same file for the
  same variant is still a no-op; for a different variant it is different work.
- **Run history records the variant per file.** `run_files` has a `variant`
  column and its primary key widened to include it — under the narrow key a run
  embedding one file four ways overwrote the same row three times. Existing
  databases are migrated on startup, rebuilt rather than altered because SQLite
  cannot widen a primary key in place.

The embedding model stays fixed for the whole run. Mixing two of them in one
index makes similarity scores meaningless, and that is a different feature from
mixing chunk shapes.

### 7.2 Resuming still works

`index_plan` compares what the file produces now against what the index already
holds **in that variant's space**. So re-indexing a variant after an interrupted
run embeds only what is missing, exactly as it does for production, and the
comparison is never against another variant's vectors.

---

## 8. Querying a variant

`POST /chat` gains `chunk_variant`. Empty is production; naming one searches
only that variant's vectors:

```json
{ "query": "What was Cold Chain revenue?", "chunk_variant": "structural-512-64" }
```

`retrieval.retrieve` resolves the variant to a space before anything is
embedded, so a bad variant fails the request rather than costing an embedding
call first. Everything else about the request is unchanged, which is the whole
design: hold the question, the model, the prompt and `top_k` still, change only
where the chunks came from, and the difference in the answer is the difference
the chunking made.

---

## 9. Scoring

Reading two answers side by side tells you which one you preferred that time.
It does not separate four strategies that are all roughly reasonable, and on a
document like an annual report they usually are.

`POST /chunking/score` puts a golden set to every variant and counts.

### 9.1 Retrieval recall is the headline

Whether the passage the answer needed came back is chunking's job. Whether the
answer reads well is the model's. Judging on the answer alone confounds the two,
and a capable model papers over a mediocre retrieval often enough to hide a real
difference.

So the ranking is on recall, and correctness breaks the tie — two variants that
retrieve equally well are separated by what the model could do with it.

### 9.2 Which section a chunk came from

The two sides have to speak the same language: a golden row cites
"SECTION 3. FINANCIAL HIGHLIGHTS", and retrieval returns a passage of text.

The mapping cannot come from the chunk's own metadata, because only one of the
four strategies knows what a section is — measuring `structural` against its own
yardstick and the others against nothing would decide the comparison before it
ran. So `services/chunk_sections` computes it from the document at scoring time,
once per run, for every variant equally.

Two details make it exact rather than approximate:

- **A chunk is located by probes, not by its whole text.** `structural`
  prepends a heading that does not appear at that point in the file, so
  searching for the chunk verbatim would fail on exactly the strategy most
  likely to win. Three short probes are taken — tail first, since no strategy
  inserts anything there.
- **A chunk can be in more than one section.** A cut that straddles a heading
  covers both, and reporting only the first would understate the recall of
  every strategy that ignores headings.

### 9.3 The scorer is not ours

`golden_scorer.score_row` already decides whether an answer is right and how
much of the gold section was retrieved, and it is the same function
`evals/run_eval.py` uses. A second one here would mean the app and the offline
harness could quietly disagree about the same answer, with nothing to say which
was lying.

`variant_scorer` supplies the middle: retrieve in one variant's space, map the
chunks to sections, optionally generate an answer through the same prompt
builder and adapter the live endpoint uses, and hand both to the scorer.

### 9.4 What is not counted

A row the document cannot answer cites no section. There is nothing to retrieve
and nothing to get wrong, so those rows are left out of the recall average
rather than counted as misses — including them would punish every variant for a
question none of them could have answered, and would flatter nothing.

A row that fails outright is recorded with its error and the run carries on.
Nineteen answers are not thrown away to punish the twentieth.

### 9.5 Cost

| Mode | Per variant, per question |
|---|---|
| `generate: false` | One embedding and one query. Fractions of a cent for a whole set. |
| `generate: true` | The above plus one model call. Four variants against forty questions is 160 model calls. |

Retrieval-only still answers the question the feature exists for, which is why
it is a choice rather than a requirement.

### 9.6 The run is not durable

There is no history table behind a score. A score describes an index at a
moment: what a variant retrieved today says nothing about what it retrieves
after the file is re-indexed, and a stored table of old scores would invite
exactly that comparison. The run is held in memory while it matters and is then
gone; re-running it is the honest way to have it again.

Variants are scored one after another rather than at once. They share an
embedding endpoint and a model, so concurrency would mostly queue at the
provider — and it would make the per-variant timings, which are part of what is
being compared, meaningless.

---

## 10. The screen

One tab, in the order the work happens.

0. **The banner** — which space answers every question, stated permanently.
   Once the answering space is a setting, "which cut am I talking to" stops
   being obvious, and a screen that mentioned it only when something was wrong
   would teach nobody where their answers come from.
1. **The bench** — pick a file (or the whole bucket), pick a strategy, set the
   geometry. Preview is
   labelled free because it is; Index all four is the primary action because
   comparing is the point.
2. **The preview** — the summary line, a bar per chunk (which is where a cut's
   shape becomes obvious at a glance), then the chunks themselves, clamped to
   three lines because a panel that prints the whole document is not a preview.
3. **Index progress** — the existing component and hook, unchanged, because it
   is the existing run.
4. **The variants** — what exists right now, read back from the index rather
   than from a table, so it is correct after a restart and cannot claim a
   namespace somebody deleted from the console. **Answer from this** adopts one
   as production; the adopted one cannot be deleted until production is pointed
   elsewhere, because a delete that left the app with nothing to answer from is
   not a decision anybody made.
5. **The scoreboard** — the ranking, then a grid of one row per question and one
   column per variant. Clicking a cell shows what that variant retrieved against
   what it should have, which is where a number becomes a fixable problem.

A/B comparison lives on the **Chat** tab instead: two columns, the same
question, model and prompt, different variants. Two rather than four, because
four columns of prose is not something a person reads — four strategies ranked
by hit-rate is, and that is the scoreboard's job. The chat comparison is for
seeing *why* one of them lost.

---

## 11. How this is tested

`backend/tests`, and it is the backend's first suite. Two tiers.

**The default tier runs offline**, in under a second, and covers everything
above: `uv run pytest`. Its fakes sit at the *vendor boundary* — a
namespace-aware in-memory index, a deterministic bag-of-words embedding, and
object storage as a dict — so the queue, the plan, the ingestion loop and every
line of `vector_store` run for real. That matters here more than usual: the
isolation this feature promises is not something the app enforces, it is
something it delegates, so a test that mocked at the call site would prove only
that the mock was called.

The claims each file is there to defend:

| File | Defends |
|---|---|
| `test_chunking_strategies.py` | Every strategy's contract — budget, contiguity, determinism, no text dropped — plus a regression pin on `boundary`, whose output every stored production vector was cut with. |
| `test_chunk_variants.py` | The identity round trip, and that a named variant beats a requested geometry. |
| `test_variant_isolation.py` | Four cuts coexisting, a query that cannot cross a namespace, production untouched, resuming per variant, and the cleanup rules. |
| `test_chunk_sections.py` | That a chunk maps back to its section under every strategy — including `structural`, whose injected heading breaks a naive lookup. |
| `test_variant_scoring.py` | The two ways a scoreboard can lie: counting an unanswerable row as a miss, and letting a failed row read as a wrong answer. |
| `test_chunking_api.py` | Which failures become which status code, and that a preview really writes nothing. |
| `test_production_pointer.py` | That the pointer is followed, refuses a space that cannot answer, and survives a restart. |
| `test_source_variants.py` | That every space holding a file is listed and judged on its own, and that the verdict follows the pointer. |
| `test_missing_index.py` | That an index which does not exist reads as empty — and is never created by a read. |
| `test_vector_store_concurrency.py` | That the Pinecone handle is never seen half-built, which is how a request reading two spaces at once used to crash. |

**The live tier is opt-in**: `uv run pytest --live`. One test, doing the real
round trip — preview, index a throwaway `320/40` variant, query it, delete it —
because a fake cannot notice the day a vendor renames an argument. It asserts
the space production answers from is unchanged, and drops what it created
however it ends.

One lesson is worth repeating, because it cost a real file: the fakes have to be
at the **client** seam, not the function seam. Several services import vendor
functions by name, one of those bindings was missed, and a test deleted a real
object out of the real bucket. Both `ObjectStoreManager.get_client` and the
embedding client are now barred outright in the harness, so a missed binding
fails a test instead of reaching a vendor.

---

## 12. Guarantees, and what is not guaranteed

**Guaranteed:**

- A query against one variant cannot return another variant's vectors, or
  production's.
- Deleting a source file removes its copies from every variant too. Deindexing
  does not — that withdraws a file from retrieval and leaves it re-indexable —
  but a file that is *gone* leaves nothing behind anywhere, or a comparison run
  would score four strategies against text nobody can look up any more.
- Nothing done on the chunking screen writes to the production index.
- The same `(strategy, geometry, text)` always produces the same chunks, so
  re-indexing a variant is idempotent and resumable.
- No chunk exceeds `chunk_size` tokens under any of the four strategies.
- Every variant in one scoring run faces the same questions, model, prompt and
  `top_k`.

**Not guaranteed:**

- **That a variant is complete.** A run that stopped partway leaves fewer
  vectors than its chunk total; the variant reports `interrupted` and should be
  re-indexed before it is scored.
- **That two variants embedded with different models are comparable.** The
  variant id does not include the embedding model, so changing the configured
  model and re-indexing replaces a variant's vectors rather than creating a new
  one. Retrieval refuses to score across two embedding spaces, so a half-migrated
  variant fails loudly rather than quietly.
- **That a score is reproducible later.** It measures the index as it stands.
- **Offsets into the raw file.** Same caveat as
  [chunking.md](chunking.md) §6 — offsets are relative to the stripped text.
