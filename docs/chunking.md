# Chunking

One source file has to become many embeddable segments, and every property the
rest of the pipeline relies on — resumable runs, staleness detection, citations
that point back at a file — is decided at the moment those segments are cut.
This document is the whole chunking design: how a file's bytes become text, how
that text is measured and cut, what one chunk carries, what survives into the
index, and what is deliberately not guaranteed.

The short version: **chunking is measured in tokens, cut at the last natural
break in the final quarter of each window, and identical for every supported
file type.** There is no per-format chunker. A `.md` file and a `.txt` file
follow byte-for-byte the same path.

That describes the **default** strategy, `boundary`, which is what an untouched
request still gets. It is now one of four, selectable per run, and a document
can be embedded under several of them at once to find out which retrieves best.
The strategies, how they are isolated from each other and from production, and
how the comparison is scored are in
[chunking-strategies.md](chunking-strategies.md). Everything below describes the
default cut and the machinery every strategy shares.

---

## 1. Where chunking runs

Two callers, and only two.

| Caller | Entry point | Purpose |
|---|---|---|
| `services/ingestion.index_source` | `chunker.chunk_document` | The embedding pipeline. Produces the `Chunk` records that become vectors. |
| `services/document_sections._slices` | `chunker.split_text` | The golden-set generator, and only for a document with no detectable headings. |

The second caller matters to the design because it is the reason `split_text`
is a separate function from `chunk_document`: the golden-set generator wants
the *cutting* without the identity, and it wants a different geometry (see §11).
Nothing else in the codebase splits text. A third splitter would be the same
job done twice, and the two could disagree about the same document.

Everything below `chunk_document` is pure: no I/O, no framework, no clock. Given
the same key, text and geometry it produces the same chunks, which is what makes
re-indexing idempotent.

---

## 2. Before chunking: bytes to text

Chunking never sees a file. It sees a string, produced by
`services/text_extraction.extract_text`, which is the only place file format is
consulted at all.

### 2.1 The extractor registry

```
.txt       -> _extract_plain_text
.md        -> _extract_plain_text
.markdown  -> _extract_plain_text
```

Three extensions, one extractor. The registry is a dict keyed on the lowercased
suffix; an unregistered extension raises `UnsupportedSourceType`, which names
the extension and lists what is supported.

`SUPPORTED_EXTENSIONS` is derived from that dict, so the gate at every other
layer moves in lockstep with the registry:

| Layer | Behaviour on an unsupported type |
|---|---|
| `services/uploads.validate` | Rejects the upload outright with a readable reason. |
| `services/sync_status` | Reports the file as `UNSUPPORTED` — visible, not hidden. |
| `services/index_queue` | Skips the file, emits a `completed` event with `skipped: true` and an `error` event at stage `extraction`. The run continues. |

A file the pipeline cannot read is a per-file status, never a failed run.

### 2.2 The encoding cascade

`_extract_plain_text` tries three encodings in order and returns the first that
decodes:

1. **`utf-8-sig`** — reads plain UTF-8 identically to `utf-8` but also strips a
   byte-order mark. Without this the BOM survives into chunk 0 as a stray
   character and into the first vector's stored text.
2. **`cp1252`** — rescues files exported from older Windows tooling.
3. **`latin-1`** — the last resort.

The result is the file's text *verbatim*: no normalisation, no whitespace
collapsing, no line-ending conversion, no markdown stripping. Whatever the file
says is what gets chunked.

### 2.3 The consequence for file types

**There is no format-aware chunking anywhere in the embedding path.** Markdown
is treated as plain text, which means:

- ATX headings (`## Section`) are ordinary lines. A heading is never forced to
  start a chunk, and a chunk boundary can fall between a heading and the text
  it introduces.
- Fenced code blocks, tables, and list structures carry no weight. A chunk may
  open mid-fence or cut a table in half.
- Front matter is text like any other, and lands in chunk 0.
- The only structure the chunker recognises is a **blank line** and **terminal
  sentence punctuation** (§4). Markdown happens to use blank lines between
  blocks, so markdown chunks tend to land on paragraph edges — but that is a
  side effect of the convention, not a rule the chunker enforces.

The one place in the codebase that *does* read markdown structure is
`services/document_sections`, which detects ruled headings, ATX headings and
plain/numbered headings to build the golden set's citable sections. That is a
separate concern and does not affect a single vector (§11).

---

## 3. The unit of measurement: tokens

Chunk size is counted in **tokens**, never characters.

- Encoding: `cl100k_base`, via `tiktoken`. Named as `DEFAULT_ENCODING` and not
  exposed through the API.
- It backs every current OpenAI embedding model, so a count here is the count
  the embedding endpoint will bill for.
- Encoders are expensive to construct and safe to share, so they are cached in
  a module-level dict keyed by encoding name (`_get_encoder`).
- `count_tokens(text, encoding_name)` is the public measure, used wherever the
  pipeline needs a token figure.

Characters were rejected because a character budget drifts against the real
token count — dense text (code, tables, CJK, long unbroken identifiers) tokenises
far more heavily than prose, so a character-sized chunk can silently overflow the
model's input limit on exactly the documents most likely to contain one.

---

## 4. The split algorithm

`chunker.split_text(text, chunk_size, chunk_overlap, encoding_name)` returns a
list of `(content, start_offset, end_offset)` tuples. This is the whole of the
cutting logic.

### 4.1 Guard and fast paths

1. **Geometry check.** `chunk_overlap >= chunk_size` raises `ValueError`. With
   an overlap at or above the chunk size the cursor cannot advance and the
   splitter would loop forever. (The HTTP layer checks the same condition first,
   so a caller gets a 400 rather than a 500 — see §9.)
2. **Strip.** The whole text is stripped once, up front. **Every offset produced
   from here on is relative to the stripped text**, not the raw file (§6).
3. **Empty text** → `[]`. No chunks, and the caller treats that as a valid
   outcome, not an error (§7.3).
4. **Short text.** If the whole document encodes to `chunk_size` tokens or
   fewer, it is returned as a single chunk spanning `(0, len(stripped))`. No
   windowing, no boundary search.

### 4.2 The windowing loop

Two cursors are tracked, and the distinction between them is load-bearing:

- `token_cursor` — position in the token list. Drives advancement and overlap.
- `cursor` — character position in the stripped text. Used only to anchor the
  offset search (§6).

Each iteration:

```
window     = tokens[token_cursor : token_cursor + chunk_size]
candidate  = decode(window)                # the full-size text of this window
is_last    = token_cursor + chunk_size >= len(tokens)
```

**If this is the last window**, the candidate is taken whole. It runs to the end
of the document, so trimming it back to a natural break would drop the
remainder — there is no following chunk to pick it up.

**Otherwise**, a natural boundary is looked for:

```
earliest = int(len(candidate) * (1 - BOUNDARY_SEARCH_FRACTION))   # 0.75 of characters
boundary = _find_boundary(candidate, earliest)
content  = candidate[:boundary] if boundary else candidate
```

The content is then stripped. An empty result is skipped (no chunk emitted) but
the loop still advances, so a window of pure whitespace cannot stall the split.

### 4.3 Boundary selection

`_find_boundary(text, earliest)` implements a strict preference order:

| Preference | Pattern | Matches |
|---|---|---|
| 1. Paragraph | `\n\s*\n` | A blank line, with whatever trailing whitespace it carries. |
| 2. Sentence | `(?<=[.!?])\s` | Terminal punctuation followed by any whitespace. |
| 3. Hard cut | — | The window's full token boundary. |

Two rules govern the search, and both exist to keep chunks close to target size:

- **The *last* qualifying match wins**, not the first. Searching forward and
  taking the first break would truncate a 512-token window back to its opening
  paragraph.
- **Only matches at or after `earliest` qualify.** `BOUNDARY_SEARCH_FRACTION`
  is `0.25`, so a break is only worth taking if it falls in the final quarter of
  the window. Beyond that the resulting chunk is too shrunken to be worth the
  natural edge, and the hard cut is taken instead.

Paragraph beats sentence unconditionally: if any blank line falls in the search
zone, no sentence break is even considered — even one that sits later in the
window and would have produced a fuller chunk.

The offset arithmetic here is honest but mixed: the window is sized in *tokens*
while `earliest` is a *character* offset derived from the decoded window's
length. So "the final quarter" is a quarter of the window's characters, which is
only approximately a quarter of its tokens.

### 4.4 Advancement and overlap

```
consumed = len(encode(content)) or chunk_size
step     = max(1, consumed - chunk_overlap)
token_cursor += step
cursor        = len(decode(tokens[:token_cursor]))
```

The step is measured against **what was actually emitted**, not against
`chunk_size`. This is the detail that makes overlap exact: when the boundary
search trims a 512-token window down to 400 tokens, the cursor advances 336, so
the next chunk still begins exactly 64 tokens before the previous one ended.
Stepping by `chunk_size - chunk_overlap` instead would open a gap of unembedded
text on every trimmed boundary.

`max(1, ...)` guarantees forward progress, and the `or chunk_size` guarantees a
skipped whitespace window still advances a full stride.

The exactness is to within a token or two rather than absolute: `content` is
re-encoded after being sliced and stripped, and token boundaries in a substring
need not line up with boundaries in the window it came from.

The loop ends when the last window has been emitted (`break`), not when
`token_cursor` passes the end — so the final chunk is always emitted whole.

---

## 5. Worked trace

Illustrative, with the boundary-search outcome assumed rather than measured.
Geometry: `chunk_size=512`, `chunk_overlap=64`. Document: 1,500 tokens.

| Iter | Window | `is_last` | Boundary found | Emitted | `consumed` | `step` | Next cursor |
|---|---|---|---|---|---|---|---|
| 1 | 0–512 | no | paragraph at ~92% | tokens 0–470 | 470 | 406 | 406 |
| 2 | 406–918 | no | sentence at ~81% | tokens 406–820 | 414 | 350 | 756 |
| 3 | 756–1268 | no | none in final quarter | tokens 756–1268 (hard cut) | 512 | 448 | 1204 |
| 4 | 1204–1500 | **yes** | not attempted | tokens 1204–1500 | — | — | loop ends |

Four chunks. Each begins 64 tokens before its predecessor ended. Chunk 4 is 296
tokens — the tail, taken as-is.

---

## 6. Offsets

Each emitted segment carries `(start_offset, end_offset)` as **character**
positions:

```
start = stripped.find(content, cursor)
start = start if start != -1 else cursor
end   = start + len(content)
```

The search starts at `cursor` rather than at 0, so a passage repeated verbatim
elsewhere in the document does not anchor the chunk to the wrong copy. A failed
search falls back to `cursor` rather than raising — an approximate offset is a
degraded highlight, a raised exception is a failed ingestion.

Two limits are worth stating plainly:

- **Offsets are relative to the stripped text.** A file with leading whitespace
  has every offset shifted by however much was stripped. A consumer highlighting
  in the raw file must account for that itself.
- **`find` can miss on a mid-character token split.** Decoding a partial token
  window can yield a replacement character where a multi-byte character was cut,
  and the same applies to the `cursor = len(decode(tokens[:token_cursor]))`
  recomputation. Both degrade to the fallback rather than failing.

---

## 7. From segment to chunk record

`chunker.chunk_document(source_key, text, chunk_size, chunk_overlap,
encoding_name)` wraps `split_text` and attaches identity.

### 7.1 Identity

```
document_id = provenance.document_id_for(source_key)   # sha1(key)[:16]
chunk_id    = provenance.vector_id_for(document_id, index)
            # -> "a1b2c3d4e5f6a7b8#00003"
```

Three rules, all owned by `services/provenance` and spelled out nowhere else:

- **The chunk id *is* the vector id.** One identity across both sides, so a
  retrieved vector traces straight back to a position in a source file with no
  join table.
- **The document id is derived, not stored.** `sha1(source_key)[:16]` — 64 bits,
  stable across runs, short enough to read in a log line. Stability is what
  makes a re-index overwrite in place instead of accumulating duplicates.
- **The chunk index is zero-padded to five digits.** Padding makes a lexical
  sort of vector ids a chunk-order sort, which is how a prefix listing comes
  back from the vector store.

### 7.2 The `Chunk` model

`schemas/chunk.Chunk`, one per emitted segment:

| Field | Value as set here |
|---|---|
| `id` | `{document_id}#{index:05d}` |
| `document_id` | `sha1(source_key)[:16]` |
| `content` | The segment text, already stripped. `min_length=1`. |
| `chunk_index` | Enumeration position, contiguous from 0. |
| `overlap` | `chunk_overlap` for every chunk except index 0, which gets 0. |
| `start_offset` | Character offset in the stripped text. |
| `end_offset` | `start_offset + len(content)`. |
| `char_count` | `len(content)`. |

`chunk_index` is assigned by `enumerate` over the *emitted* segments, so indices
are always contiguous — a skipped whitespace window leaves no gap in the
numbering.

### 7.3 The empty case

A file that extracts to nothing (or to whitespace only) produces zero chunks.
`ingestion.index_source` treats that as **indexed with no vectors**: it deletes
whatever a previous run left behind for the key, logs the count removed, emits
the `upserting` stage and returns. An emptied file therefore stops answering
queries rather than continuing to serve its old text.

---

## 8. What survives into the index

Chunking produces eight fields per chunk. The index stores four of them.

`ingestion._records_for` builds one upsert record per chunk:

```
id       = vector_id_for(document_id, position)      # the chunk id, again
values   = the embedding vector
metadata = provenance.build_metadata(...)
```

and `build_metadata` writes:

| Metadata key | Source |
|---|---|
| `source_key` | The object key — the one field authoritative on both sides. |
| `document_id` | Derived from the key. |
| `chunk_index` | The chunk's position. |
| `chunk_total` | How many chunks the **whole file** produced this run. |
| `content` | The chunk's text, so retrieval can return it without a second read. |
| `source_etag` | The object's etag **at embed time**. |
| `source_last_modified` | Epoch seconds, at embed time. |
| `embedded_at` | Epoch seconds, stamped once per file per run. |
| `embedding_model` | Added by `vector_store.upsert_chunks`, which owns that key. |

**Dropped, and worth knowing:** `start_offset`, `end_offset`, `char_count` and
`overlap` exist only in process. Nothing persists them. `SourceChunk` — what
`GET /sources/{key}` returns — recomputes `char_count` from the stored text and
carries no offsets at all. A consumer that wants to highlight a retrieved chunk
in its source file must re-derive the offsets by re-chunking, which is only
correct if the geometry matches the one the run used, and that geometry is
recorded per-run in `runs.db`, not per-chunk.

`chunk_total` is the field that makes a partial run detectable: every chunk
carries the number the whole file should have, and `sync_status` compares it
against the number of vectors actually present. Disagreement reports the file
as `INTERRUPTED`, which outranks every other verdict — the other metadata all
describes the *file*, so none of it can reveal that only some of the file's
chunks were written.

---

## 9. Configuration surface

| Parameter | Default | Range | Where |
|---|---|---|---|
| `strategy` | `boundary` | the four in the registry | `IndexRequest`, per run |
| `chunk_size` | `512` tokens | 64–8000 | `IndexRequest`, per run |
| `chunk_overlap` | `64` tokens | 0–4000 | `IndexRequest`, per run |
| `variant` | none — production | any variant id | `IndexRequest`, per run |
| `encoding_name` | `cl100k_base` | — | Not exposed; `chunking.tokens.DEFAULT_ENCODING` |
| `BOUNDARY_SEARCH_FRACTION` | `0.25` | — | Not exposed; `chunking.boundary` constant |

A named `variant` overrides the first three outright and decides where the
vectors land; the precedence is settled in `chunk_variants.resolve` and nowhere
else. See [chunking-strategies.md](chunking-strategies.md) §2.

Defaults live in `services/chunking/tokens` as `DEFAULT_CHUNK_SIZE` /
`DEFAULT_CHUNK_OVERLAP` and are imported by both `schemas/ingestion` and
`schemas/chunking`, so the API's documented default and the splitter's default
cannot drift apart. That module is a leaf — it imports nothing of ours — which
is what lets a schema read a default from it without dragging the strategy
registry into its own import.

512 was chosen so a retrieved chunk is specific rather than a wall of text, while
still being large enough to hold a whole argument. 64 is a couple of sentences —
enough that a sentence spanning a boundary appears whole on one side of it.

Validation happens twice, deliberately:

- `routers/sources.index_sources` rejects `overlap >= size` with **400** before
  enqueuing. Once a run is enqueued its progress is an SSE stream, and by the
  time a stream opens its status code has already been sent — a bad request
  could not be reported.
- `split_text` raises `ValueError` on the same condition, so the service stays
  correct when called from anywhere other than that router.

Geometry is recorded per run in `run_store` (`chunk_size`, `chunk_overlap`
columns), which is the only durable record of how a given file's vectors were
cut.

---

## 10. Chunking's contract with the rest of the pipeline

Three downstream behaviours depend on properties chunking provides.

**Resumable runs.** `index_plan.plan_for` compares the chunks the file produces
*now* against the text already stored at each position. A position matches when
the stored text is byte-identical **and** the stored vector came from the
requested model. Matching on text rather than on a file fingerprint is what makes
this exact: a partially written document can hold chunks from two versions of the
file at once, and no per-file field distinguishes those, whereas identical text at
the same position is proof.

This is why chunking must be deterministic. Any change to the geometry, the
boundary rules, or the encoding invalidates every stored comparison and turns the
next run into a full re-embed.

**A vector id names a slot, not a text.** The same `{document_id}#{nnnnn}` holds
different text after a re-index at a different chunk size. Anything recording
what a chunk said must record `provenance.content_fingerprint` alongside the id,
or it cannot tell a stable chunk from a silently replaced one.

**Chunk 0 is written last.** It carries the fingerprint and `chunk_total` that
staleness detection reads, so writing it last means an interrupted write leaves
the *old* values in place and the file honestly reports itself stale. Written
first, a half-written file claims to be current while serving text from a version
that no longer exists. `ingestion._write_order` enforces it, and
`index_plan.plan_for` forces chunk 0 into the embed set whenever the chunk count
changes — even when its own text did not — so a file that merely grew does not
carry an out-of-date total and report itself interrupted forever.

Pruning runs **after** the upsert, so a document is never briefly missing a chunk
it actually has.

---

## 11. The second geometry: golden-set sections

`services/document_sections` cuts the same documents for a different purpose —
the titled sections a golden row cites — and it reaches for the chunker only as
a fallback.

Its order of preference:

1. **Ruled headings** — a line of `=`, `-`, `_`, `*` or `~` (8+ characters) above
   or below a short line.
2. **ATX headings** — markdown `#`, with the depth carrying the *most* headings
   chosen as the section level, so a lone `#` title above five `##` sections does
   not fold the document into one.
3. **Plain headings** — numbered (`3.1 Eligibility`), keyword-led (`ARTICLE 2`),
   or fully capitalised lines.
4. **`chunker.split_text`**, when the document marks no structure at all.

The fallback uses a deliberately different geometry:

| | Embedding path | Golden-set fallback |
|---|---|---|
| `chunk_size` | 512 tokens | **400** tokens (`FALLBACK_SLICE_TOKENS`) |
| `chunk_overlap` | 64 tokens | **0** |

Overlap is the one place the two purposes want opposite things. Retrieval repeats
tokens across a boundary so a sentence spanning it survives whole in one chunk.
Drafting would read that repetition as more document and ask the same question
twice.

The size is set to a long *section* rather than to an embedding chunk, because
this is the unit a question gets written about, not the unit that gets retrieved.

---

## 12. Guarantees, and what is not guaranteed

**Guaranteed:**

- No chunk exceeds `chunk_size` tokens under the configured encoding.
- Chunk indices are contiguous from 0, in document order.
- The same `(source_key, text, geometry)` always produces the same chunks, with
  the same ids.
- Consecutive chunks overlap; the overlap is measured against the emitted chunk,
  not the nominal window.
- The split terminates: `step >= 1`, and `overlap < size` is validated at both
  the HTTP and service boundary.
- No text between the first and last chunk is dropped.

**Not guaranteed:**

- **A minimum chunk size.** The floor is a quarter of the window's *characters*,
  which is only approximately a quarter of its tokens; and the final chunk has no
  floor at all.
- **Exact token overlap.** Re-encoding a sliced-and-stripped substring can shift
  a boundary by a token or two.
- **Byte-exact offsets** into the raw file — offsets are relative to the stripped
  text, and degrade to a cursor fallback on a mid-character token split.
- **Structural integrity.** A chunk may open mid-code-fence, mid-table, or
  between a heading and its body.
- **Stability across geometry changes.** A vector id is a slot; the same id holds
  different text after a re-index at a different size.

---

## 13. Known rough edges

Recorded because they are real, small, and easy to fix wrong.

- **`Chunk.overlap` is documented in the wrong unit.** The schema describes it as
  "Number of overlapping characters with the preceding chunk"; the value written
  is tokens.
- **`Chunk.overlap` records the request, not the reality.** It is set to the
  configured `chunk_overlap` for every chunk after the first, regardless of what
  the boundary search actually produced.
- **A binary file with a supported extension is not rejected.** `latin-1` maps
  every byte, so the cascade in `_extract_plain_text` never reaches its
  `UnsupportedSourceType(".bin")` branch — a `.txt` file holding binary decodes
  to mojibake and gets embedded.
- **`Document`** (`schemas/document.py`) is not used by this path. Chunking goes
  straight from extracted text to `Chunk`; nothing constructs a `Document`.
- **The boundary arithmetic is now pinned, but only by its output.** The
  smoke suite asserts the report still cuts into eight chunks under `boundary`
  and that no chunk exceeds the budget, which would catch a change to the
  search fraction or the cursor advancement. It does not test `_find_boundary`
  or the token cursor directly, so *why* a cut moved still has to be worked out
  by hand.
- **Chunk text is stored twice** — in the source file and in vector metadata. See
  [chunk-text-migration.md](chunk-text-migration.md) for the plan that moves it
  out of the vector store, which is also what would make hybrid keyword search
  possible.

---

## 14. Adding a file type

The registry is the whole extension point.

1. Add an extractor `bytes -> str` to `services/text_extraction`.
2. Register it in `_EXTRACTORS` under its lowercased extension.
3. Add the dependency.

Nothing else changes. `SUPPORTED_EXTENSIONS` updates itself, uploads start
accepting the type, `sync_status` stops reporting it as unsupported, and the
chunker needs no knowledge of it — it receives a string like every other file.

Never branch on extension at a call site. If a format needs structure-aware
cutting rather than the shared token split, that is a strategy: a module under
`services/chunking`, a member on `ChunkStrategy` and a line in the registry —
not an `if` in `chunk_document`. See
[chunking-strategies.md](chunking-strategies.md) §5, which also covers the
import-time check that stops a strategy being offered without an implementation
behind it.
