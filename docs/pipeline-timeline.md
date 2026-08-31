# Pipeline Timeline

A streaming answer hides its own latency. `POST /chat` sent a trace id, then the
retrieved chunks, then text — and between them, several seconds of nothing that
no client could account for. Embedding, the vector search and the wait for the
provider's first token were one undifferentiated spinner.

Every step of the pipeline now reports itself on the wire as it happens: a
`stage` event when it starts, and a second when it ends carrying how long it
took and one line on what it produced. The chat screen renders them as a
timeline that fills in live.

The design constraint that shaped everything below: **adding a stage must be a
single change, at the place the work runs.** Nothing in the router, the event
schema, or the UI enumerates the stages, so a step added later appears on the
client by itself.

---

## 1. What the user sees

A **Pipeline** panel above the citations, from the moment a question is asked:

```
Pipeline                                        1.21s accounted for
 ●  Embedding the question   text-embedding-3-small · 1536 dimensions   0.41s
 ●  Searching the index      5 match(es) returned                       0.28s
 ●  Ranking the matches      3 kept, 2 below 0.4                        0.00s
 ●  Building the prompt      3 chunk(s) in context                      0.00s
 ⠋  Generating the answer                                                  …
```

- A step spins while it runs, turns green when it finishes, red with the failure
  message when it fails.
- The right column is that step's own duration; the header totals the steps that
  have finished, so the number does not move while one is still running.
- The middle column is the step's own account of what it did, truncated with the
  full text on hover.
- Rows appear as the work happens, not in one batch at the end.

---

## 2. The stages sent today

| `name` | Label | Runs | `detail` |
|---|---|---|---|
| `embedding` | Embedding the question | Retrieval, first | model and vector dimensions |
| `search` | Searching the index | Retrieval, after embedding | how many matches the store returned |
| `ranking` | Ranking the matches | Retrieval, last | how many kept, how many below the threshold |
| `context` | Using the supplied context | Instead of the three above, when the client sent its own chunks | how many chunks it sent |
| `prompt` | Building the prompt | Before generation | how many chunks went into the context |
| `generation` | Generating the answer | The provider call | time to first token, and the number of deltas |

With RAG off, no retrieval stages are sent at all — `prompt` and `generation`
are the whole timeline.

`generation` reports time-to-first-token separately because it is the wait a
person actually feels, and it is invisible in the stage's total.

---

## 3. The event

`event: stage`, payload `StageEventData`:

| Field | Meaning |
|---|---|
| `sequence` | Position in the timeline. The start and end of one step share it. |
| `name` | Stable machine id, safe to branch on. |
| `label` | Display wording, written by the stage, rendered as-is. |
| `status` | `started`, `completed` or `failed`. |
| `elapsed_ms` | Duration; `0` on the `started` event. |
| `detail` | What the step produced — or the failure message when `failed`. |

Two events per step. They share a `sequence` so a client replaces a row rather
than appending a second one.

The **label travels on the wire**. That is the mechanism the extensibility rests
on: a client that mapped `name` to its own wording would silently drop every
stage added afterwards, so the server sends text ready to display and the client
renders whatever arrives.

### Ordering

`stage` events interleave with the rest of the stream. The contract already
guaranteed by the endpoint is unchanged: `trace` first, `retrieval` before the
first text delta, `usage` last.

### Failure

A step that raises ends as `status: "failed"` with the exception message as its
`detail`. The existing `error` event still follows where the failure is
survivable — the stage event says *which step* broke, the error event says the
request is continuing without it. Retrieval failing leaves the failed step
visible and the answer streams ungrounded; generation failing ends the stream.

---

## 4. How a stage is added

Wrap the work, where the work is:

```python
async with timeline.stage("guardrail", "Checking the question") as stage:
    verdict = await screen(query)
    stage.note("passed" if verdict.ok else verdict.reason)
```

That is the entire change. The step streams to the client, renders with its
label, and is logged — with no edit to the router, the schema, the docs union,
or the frontend.

Two rules for a service that wants to do this:

- Take `timeline: Optional[Timeline] = None` and fall back to
  `pipeline_timeline.detached()`, so the same function still runs outside a
  request (an evaluation, a script) with nowhere to report to.
- For a step that runs inside a service the router awaits, that is all. For work
  the router drives itself, the router drains the timeline where it can write a
  frame.

---

## 5. Getting the events out while the work runs

The awkward part, and the reason `Timeline` exists rather than a list of
timestamps: a service reports stages from *inside* a call the router is
awaiting. Buffer them and the client learns about a two-second embedding two
seconds after it mattered.

`Timeline` records events into a buffer and wakes anyone waiting on one. The
router consumes them two ways:

- **`follow(task)`** — an async generator that yields events *as they are
  recorded* while the task runs, then the remainder once it ends. Used for the
  retrieval call. The task is created by the router so it can read the result
  (or its exception) afterwards; `follow` cancels it if the consumer goes away,
  which is what a client hanging up mid-request looks like.
- **`drain()`** — takes everything buffered since the last call. Used where the
  router itself owns the step and can write a frame at the right moment: it
  drains immediately inside the `generation` stage so that step is announced
  *before* the first token, rather than after the slowest wait in the request.

`follow` waits on an `asyncio.Event` rather than a queue, and re-checks the
buffer after clearing the flag, so an event recorded in that window is not lost.
Measured on a stubbed pipeline: events surfaced at +0ms, +501ms, +501ms,
+1003ms across two half-second stages — live, not batched.

`Timeline` imports nothing from FastAPI. It records events; the caller decides
how they reach a wire.

---

## 6. Logging

Every stage start and end goes to the log as well as the stream, which satisfies
the project's standing rule that a pipeline stage that leaves no trace cannot be
debugged after the fact:

```
INFO app.services.pipeline_timeline | stage embedding started — Embedding the question
INFO app.services.pipeline_timeline | stage embedding completed in 3127ms — text-embedding-3-small · 1536 dimensions
INFO app.services.pipeline_timeline | stage search completed in 2652ms — 0 match(es) returned
```

---

## 7. Files

**New — backend**

| File | Holds |
|---|---|
| `app/services/pipeline_timeline.py` | `Stage`, `Timeline` (`stage`, `drain`, `follow`), `detached()`. Framework-agnostic. |

**New — frontend**

| File | Holds |
|---|---|
| `src/features/chat/StageTimeline.tsx` | The panel. Renders whatever arrives; knows no stage names. |

**Changed**

| File | Change |
|---|---|
| `app/schemas/chat.py` | Adds `StageEventData` and `ChatStreamStageEvent`. |
| `app/services/retrieval.py` | Takes an optional `timeline`; wraps embedding, search and ranking, each noting what it produced. The embedding-model mismatch check moved inside the search stage so a mismatch is reported against the step that caused it. |
| `app/routers/chat.py` | Creates the timeline, follows the retrieval task, wraps the prompt build and the generation loop, serialises stage events. Generation failure is now marked on the stage and reported after it closes, rather than returning from inside it. |
| `app/docs/chat.py` | `stage` added to the event union, the ordering table, the guarantees, and the example stream — including the warning that the stage set will grow and must not be enumerated. |
| `frontend/src/api/types.ts` | `StageEventData`, `StageStatus`, and `stage` on the `ChatEvent` union. |
| `frontend/src/hooks/useChat.ts` | Exposes `stages`, upserted by `sequence`. No filtering, no relabelling. |
| `frontend/src/features/chat/ChatView.tsx` | Renders the panel above the citations. |
| `README.md` | The Chat feature list and the endpoint table. |

---

## 8. Verified

- A real `POST /chat` against the running backend streamed all five steps in
  order, each with its start and end event, followed by the answer and `usage`.
  Sample from that run:

  ```
  event: stage
  data: {"sequence":1,"name":"embedding","label":"Embedding the question",
         "status":"completed","elapsed_ms":3127,
         "detail":"text-embedding-3-small · 1536 dimensions"}
  ...
  event: stage
  data: {"sequence":5,"name":"generation","label":"Generating the answer",
         "status":"completed","elapsed_ms":2399,
         "detail":"first token in 2.19s · 23 deltas"}
  ```

- `Timeline` exercised directly for: normal completion, a raising stage
  (`failed` recorded, exception still propagates to the caller), a consumer that
  stops early (the underlying task is cancelled rather than left running), and
  live delivery timing.
- Stage logging confirmed in the server log.
- OpenAPI document builds with the new component schemas registered.
- Frontend `tsc` clean, `oxlint` clean, production build clean.

---

## 9. Limits

- **Not persisted.** The timeline is per-request and lives only on the stream
  and in the log. Traces still record `retrieval_ms`, `generation_ms` and
  `total_ms` — three numbers, not the per-stage account. Judging a slow answer
  after the fact still means reading the log.
- **The header total is a sum of stages, not wall-clock.** The gaps between
  steps are small but real, so it reads *accounted for* rather than *total*.
- **`ranking` and `prompt` report `0.00s`.** They are genuinely that fast; they
  are shown because the pipeline's shape is the point, not only its cost.
- The existing "Writing…" line under the answer now overlaps with the
  `generation` row. Harmless, and left alone.
