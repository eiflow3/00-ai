# Golden Set Generator

`evals/golden/meridian-fy2025.jsonl` — 40 questions, seven types, deliberate
distractors — was written by hand. Every document added to the corpus needs the
same treatment, and hand-writing it does not scale.

A golden set can now be generated from a source file, checked against that file
automatically, reviewed by a person, and downloaded as the JSONL the offline
harness already reads. It lives on a **Golden Sets** tab, fourth in the nav.

The risk that shapes every decision below: a golden set is what all future eval
scores are measured against, so **one wrong answer key silently marks correct
answers wrong from then on, and nobody finds out.** The model drafts. Code
decides what is true. A person signs off.

---

## 1. What the user sees

**Sources | Chat | Evaluations | Golden Sets | Prompts**

- Pick a source file and press **Generate**. The file does not have to be
  indexed — indexing decides what can be *retrieved*, a golden set is about what
  the document *says*.
- Progress streams as it drafts: reading the file, finding its sections,
  indexing what it states, then one line per section drafted, then the checks.
  Closing the tab does not stop the run; reopening resumes the stream.
- Every row arrives with a verdict. **Grounded** means every claim in it was
  found in the document. **N to check** means it failed that many checks, and
  each one says what was wrong in a sentence.
- A row expands into an editor for the fields most often wrong — question,
  answer, answer keys, cited sections. **Save and re-check** re-runs every
  check, so a fix either clears the flag in that response or does not clear it.
- **Accept** / **Drop** per row. Dropping renumbers the rest, so the exported
  ids never have gaps.
- **Download** gives `<slug>.jsonl`. Save it to `evals/golden/` and score
  against it with `run_eval.py --golden`.

A flagged row is not a rejected one. Most flags are a good question with one bad
field, so nothing is ever silently discarded — that would hide both the question
and the fact that the model keeps getting that field wrong.

---

## 2. The six stages

A run is a job, not a response. `POST /golden/runs` returns `202` with a job id;
progress comes from `GET /golden/runs/{job_id}/stream`, which any client can
open and reopen. Drafting is about a dozen model calls, and doing that inside
the POST would mean a reloaded tab threw all of it away.

| Stage | What it does |
|---|---|
| `extract` | Reads the file from object storage and decodes it. |
| `segment` | Cuts it into the titled sections a row is allowed to cite. |
| `facts` | Indexes every figure with the line it sits on. |
| `draft` | Three model passes (below). |
| `validate` | Grounds every drafted row in the source. |
| `self_check` | Scores each row against its own answer, using the harness's scorer. |

A pass that fails is reported and the run carries on. Eleven sections drafted
and one failed is a set worth reviewing; aborting would throw away eleven
sections of model calls to punish the twelfth.

---

## 3. Three drafting passes

Three, because the three kinds of question need three different views of the
document, and mixing them produces worse questions than asking separately.

| Pass | Sees | Produces |
|---|---|---|
| Per section, one call each | the section verbatim, the outline, that section's figures | `lookup`, `temporal`, `distractor`, `synthesis` |
| Cross section, one call | the fact digest and the outline — **never the prose** | `multi_hop`, `arithmetic` |
| Unanswerable, one call | the outline and the digest | `unanswerable` |

The cross-section pass is deliberately starved of prose. A model handed the
whole document writes questions it can answer by quoting; handed only figures,
it has to join or compute.

An `arithmetic` row must carry a **derivation** — operands, an operator from
`sum | difference | ratio | percent_of | percent_change`, and a one-line
explanation. The validator recomputes it, so working that does not add up is
rejected. The derivation is never exported; the harness scores the answer, not
the working.

### Quotas come from the document

Left to itself, a model asked for "a good set of questions" pads to whatever
number it was shown, inventing figures once the document runs out of real ones.
So the quota is computed instead:

- per section: `chars / 500`, clamped to 1–5
- cross section: one per section, clamped 2–12
- unanswerable: 0.45 per section, clamped 2–8
- **arithmetic is dropped entirely** below 12 distinct figures in the document

On the Meridian report this lands on 24 single-section + 11 cross-section + 5
unanswerable = **40 rows**, which is the size of the hand-written set, with the
same 24 single-section split. On `data/00-traditional-rag.txt` — 213 words, no
figures — it lands on 10 rows with arithmetic disabled. `density` (0.5–2×)
nudges the quotas; it cannot override the rule.

---

## 4. The checks

Each row is compared against the document, never against itself.

| Check | Rule |
|---|---|
| `keys_verbatim` | every answer key appears in the document verbatim |
| `keys_in_section` | each key appears in one of the row's own cited sections, not merely somewhere |
| `numeric_grounded` | the figure is stated, or its derivation's operands are stated and recompute to it |
| `sections_exist` | every cited section is one the splitter actually produced |
| `forbidden_grounded` | the trap is real in the document, and the answer does not spring it |
| `refusal_shape` | the `unanswerable` type and the `must_refuse` flag agree |
| `no_duplicates` | no row asks nearly the same question as another |
| `self_check` | **the row's own answer, scored by the real scorer, passes the row** |

`keys_verbatim` is the one that matters most in practice. A model writes
"$2.8 billion" where the report says `2,833.0`, and the result reads perfectly
while being a question no correct answer can ever pass.

`self_check` is the strongest and the cheapest, because it uses the harness's
own scorer rather than an approximation. It is the same guarantee
`evals/predictions/example-perfect.jsonl` gives the hand-written set.

### The scorer moved

`score_row`, `looks_like_refusal` and `REFUSAL_PATTERNS` now live in
`backend/app/services/golden_scorer.py`. `evals/run_eval.py` is the command line
and the report; it imports them. Two copies would have drifted, and the drift
would have been silent — the offline harness reporting a score the in-app
self-check no longer agreed with, with nothing to say which was lying.

That module is stdlib-only and imports no schemas, so `run_eval.py` still runs
from a bare checkout with none of the backend's dependencies installed. Both
reference prediction files score exactly as before the move.

---

## 5. Export shape

One JSON object per line, in the field order the hand-written set uses, so a
generated file and a hand-written one are interchangeable:

```
id, type, difficulty, question, answer, numeric_answer, numeric_tolerance,
answer_keys, forbidden_keys, must_refuse, gold_sections, note
```

Optional fields are **omitted rather than nulled** — `null` would score the same
but would make every generated file visibly a different kind of thing.
`gold_sections` is always present, even empty, because that is how the
hand-written set writes an unanswerable row.

Dropped rows are excluded; the remaining ids are contiguous. Rows still pending
review are included — the file is a draft until someone says otherwise, and
withholding it would mean nobody could try it in the harness.

Round-tripping the hand-written set through `build_line` reproduces 39 of its 40
rows byte for byte. The one difference is an empty `forbidden_keys: []` that is
omitted, plus one row whose key order in the source file differs from the rest
of that file — both semantically identical, confirmed by scoring both reference
prediction files against the rebuilt file and getting the same numbers.

---

## 6. Prompts

The three drafting prompts are editable on the **Prompts** tab, which is now
grouped into **Chat pipeline** and **Golden set generator**. They are the one
real knob on output quality — everything downstream only *checks*, and the
validator can tell a paraphrased key from a verbatim one but cannot make the
model ask a better question.

| id | Label | Required variables |
|---|---|---|
| `golden_section` | Section questions | `{section_text}`, `{outline}`, `{count}`, `{types}` |
| `golden_cross_section` | Cross-section questions | `{outline}`, `{facts}`, `{count}`, `{types}` |
| `golden_unanswerable` | Unanswerable questions | `{outline}`, `{facts}`, `{count}` |

They use the same store, the same validation and the same reset as the chat
prompts, so a template that drops `{count}` or names a variable the pipeline
does not supply is refused when saved rather than failing mid-run.

The assembled-request preview stays under the chat group alone: only those four
prompts become a request.

---

## 7. Layout

```
backend/app/
  schemas/golden.py              rows, sets, requests, streamed events
  services/
    golden_scorer.py             the scorer, shared with evals/run_eval.py
    document_sections.py         splits a document into citable sections
    golden_facts.py              indexes every figure and section body
    golden_catalog.py            the vocabulary, and the quota rules
    golden_generator.py          the three drafting passes
    golden_validator.py          the checks
    golden_export.py             the JSONL wire shape
    golden_db.py                 SQLite schema and connection
    golden_store.py              what a set holds, and Q-number assignment
    golden_queue.py              the run, its stream, and row re-checking
  docs/golden.py                 OpenAPI text and the event union
  routers/golden.py              12 endpoints

frontend/src/
  features/golden/               GoldenView, GenerationPanel, GoldenRowEditor,
                                 IssueBadge, checks
  hooks/useGoldenSets.ts         sets, rows, and review decisions
  hooks/useGoldenRun.ts          the run and its stream
```

Two details in the store worth knowing. **Q-numbers are assigned, never
accepted** — a model asked to number its own questions produces collisions and
gaps, and the harness keys everything by id. And **a row's number is not its
identity**: edits address `row_id`, because dropping row seven renumbers
everything after it and an API keyed on the exported number would silently
retarget every pending edit.

The golden database is its own file and is **never pruned**. Run history rolls
at thirty days and unjudged traces with it; a golden set is the answer key past
scores were measured against, so expiring one would retroactively remove the
meaning of results someone is still quoting.

---

## 8. Endpoints

```
GET    /golden/options                        types, difficulties, check names
POST   /golden/runs                           202 -> {job_id, set_id}
GET    /golden/runs/{job_id}                  where a run stands
GET    /golden/runs/{job_id}/stream           SSE, resumable via ?after=
DELETE /golden/runs/{job_id}                  stop a run
GET    /golden/sets                           list
GET    /golden/sets/{set_id}                  set + rows + issues
PATCH  /golden/sets/{set_id}                  rename the exported file
PATCH  /golden/sets/{set_id}/rows/{row_id}    edit or judge one row
GET    /golden/sets/{set_id}/export           NDJSON download
DELETE /golden/sets/{set_id}                  withdraw (soft)
POST   /golden/sets/{set_id}/restore          undo a withdrawal
```

---

## 9. What this does not do

- **No LLM judge.** Scoring stays string and number matching, so a run from six
  months ago is comparable to one from today. A judge that is itself a model
  makes that untrue the moment the judge changes.
- **`synthesis` rows are still weakly checked.** They are graded on keyword
  presence, the same limitation the hand-written set has. Read those answers.
- **Section subsections are detected but not exercised by this corpus.** Both
  corpus files use one heading depth, so `subsections` comes back empty for
  them; the field populates for a document that mixes `##` and `###`.
- **A set is filled once.** Re-running against a source opens a new set rather
  than overwriting one someone may already have reviewed.
