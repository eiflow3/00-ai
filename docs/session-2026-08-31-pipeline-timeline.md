# Session record — 2026-08-31

Streamed pipeline stages on `POST /chat`, and the client timeline that renders
them.

The feature itself is documented in
[`pipeline-timeline.md`](./pipeline-timeline.md). This is the record of the
session: what was asked, what was decided, what actually changed, and what was
left open.

---

## 1. The request

> On the `POST /chat` endpoint can we surface the process on the client side
> like a timeline including the latency or time it takes to do that specific
> process as event — `=> embedding the query 0.5s`, `=> retrieving responses
> 0.5s`, `=> generating response 0.5s` — so when I add stages on the current
> pipeline I will see it in the client.

The example given was a guardrail (`=> checking for * 0.5s`, `=> * guardrail
pass`), explicitly as an illustration and not as work to do. No guardrail was
built.

Two requirements, and the second is the harder one:

1. Report each step of the pipeline with its latency, as stream events.
2. A step added to the pipeline later must appear on the client **without a
   change on the client**.

---

## 2. What the pipeline looked like before

`POST /chat` already streamed four event types — `trace`, `retrieval`, text
deltas, `usage` — and recorded three coarse durations on the trace
(`retrieval_ms`, `generation_ms`, `total_ms`), readable only after the fact.

The steps themselves were invisible. `retrieve()` embedded the query, searched
Pinecone and ranked the results behind one call, and the wait for the provider's
first token was inside the router's stream loop. On screen: a spinner, then
text.

---

## 3. Decisions taken

**Stage labels are sent by the server, not looked up by the client.** This is
what makes requirement 2 hold. A client that mapped `name` → its own wording
would silently drop every stage added afterwards, so `StageEventData` carries
`label` as display-ready text and the client renders whatever arrives. The docs
state this as a contract: clients must not enumerate the stages they know about.

**Two events per step, sharing a `sequence`.** A `started` event makes the
slowest step visible while it is still running; the `completed` / `failed` event
carries the duration. Sharing `sequence` lets the client replace a row rather
than append.

**Stages are declared where the work is.** `retrieve()` takes an optional
`Timeline` and wraps its own three steps. Nothing central lists them — adding a
stage is one `async with timeline.stage(...)` at the place it runs.

**Events go out as they are recorded, not batched at the end.** This forced a
small piece of machinery (`Timeline.follow`) rather than a list of timestamps:
the retrieval call is run as a task so the router can yield its stage events
while it is still working. A stage reported after the fact is not progress.

**Retrieval was split into three reported steps, not one.** `embedding`,
`search`, `ranking` — because "retrieval took 3s" was the exact thing that was
already unhelpful.

**Not persisted.** Per-stage timings go to the stream and the log. Extending the
trace schema was out of scope for the ask and would need a migration; the
existing three columns are untouched.

---

## 4. What changed

**New**

| File | Lines | Holds |
|---|---|---|
| `backend/app/services/pipeline_timeline.py` | 207 | `Stage`, `Timeline` (`stage`, `drain`, `follow`), `detached()`. No FastAPI import. |
| `frontend/src/features/chat/StageTimeline.tsx` | 99 | The panel. Knows no stage names. |
| `docs/pipeline-timeline.md` | 236 | The feature doc. |

**Changed**

| File | Change |
|---|---|
| `backend/app/schemas/chat.py` | `StageEventData`, `ChatStreamStageEvent`. |
| `backend/app/routers/chat.py` | Creates the timeline; follows the retrieval task; wraps the prompt build and generation loop; serialises stage events. Generation failure is now marked on the stage and reported after it closes. |
| `backend/app/services/retrieval.py` | Optional `timeline`; three wrapped steps; the embedding-model mismatch check moved inside the search stage. |
| `backend/app/docs/chat.py` | `stage` in the event union, the ordering table, the guarantees, the example stream. |
| `frontend/src/api/types.ts` | `StageEventData`, `StageStatus`, `stage` on `ChatEvent`. |
| `frontend/src/hooks/useChat.ts` | Exposes `stages`, upserted by `sequence`. |
| `frontend/src/features/chat/ChatView.tsx` | Renders the panel above the citations. |
| `README.md` | Chat feature list and the endpoint table. |

The stages sent today: `embedding`, `search`, `ranking` (or `context` when the
client supplies its own chunks), `prompt`, `generation`.

---

## 5. Verification run in this session

- **Live `POST /chat` against the running backend.** All five steps streamed in
  order, start and end each, then the answer and `usage`. Real payloads:
  `embedding` 3127ms (`text-embedding-3-small · 1536 dimensions`), `search`
  2652ms, `generation` 2399ms (`first token in 2.19s · 23 deltas`).
- **`Timeline` exercised directly** for normal completion, a raising stage
  (`failed` recorded, exception still propagates), and a consumer that stops
  early (the underlying task is cancelled, not left running).
- **Live delivery timing**, on a stubbed two-stage pipeline: events surfaced at
  +0ms, +501ms, +501ms, +1003ms — not batched.
- **Stage logging** confirmed in the server log, one line per start and end.
- **OpenAPI** builds with `ChatStreamStageEvent` and `StageEventData`
  registered.
- **Frontend** `tsc` clean, `oxlint` clean, production build clean.

---

## 6. Left open

- **Nothing was committed.** The working tree also carries the editable-prompts
  work, which was already in flight when this session started
  (`app/routers/prompts.py`, `app/services/prompt_*.py`,
  `src/features/prompts/`, and shared edits to `api/types.ts`, `api/client.ts`,
  `App.tsx`). Those are not from this session and were left untouched — the two
  sets of changes will need separating before a commit.
- **Per-stage timings are not on the trace.** Say the word and they become a
  table beside the trace, judgeable after the fact rather than only tailable in
  the log.
- **The "Writing…" line under the answer** now duplicates the `generation` row.
  Left alone as pre-existing.
- **The roadmap is unchanged** — this was not one of its three items.
