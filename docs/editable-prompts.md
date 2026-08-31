# Editable Prompts

The wording the Generation phase sends used to be constants in
`app/services/prompt_builder.py`. Changing how an answer is grounded meant a code
edit and a redeploy — for the part of a RAG pipeline you tune most often.

Every prompt is now a record: a default that ships with the code, an optional
override saved from the client, and the variables the text may interpolate. They
are read and written on a **Prompts** tab, fourth in the nav.

---

## 1. What the user sees

**Sources | Chat | Evaluations | Prompts**

- Every instruction the pipeline sends, listed in the order it sends them.
- Each prompt shows its text, what it is for, when it is used, and whether it
  has been overridden.
- **Save**, **Discard** and **Reset to default** per prompt. An edit applies to
  the next question asked — no restart, no deploy.
- The placeholders a template may use are listed beside the box and click to
  insert, because a missing brace is the commonest rejection.
- An **Assembled request** panel at the bottom renders the current templates
  into the exact message list the model receives, using stand-in chunks.
  Chunk count is switchable 0–3, and a **RAG off** toggle previews the
  ungrounded path.
- A prompt is marked `Edited` only while it actually differs from its default,
  so an improved default arrives without having to be re-accepted.

---

## 2. The four prompts

| id | Label | Applies when | Variables |
|---|---|---|---|
| `system` | System prompt | First message on every request, unless the caller sends its own. | — |
| `context_block` | Context block | Once per request, when retrieval returned at least one chunk. | `{chunks}` *(required)* |
| `chunk_format` | Chunk format | Once per retrieved chunk, joined into `{chunks}`. | `{content}` *(required)*, `{chunk_id}`, `{score}`, `{source}`, `{document_id}`, `{rank}` |
| `no_context` | Empty retrieval fallback | Instead of the context block, when retrieval was asked for and returned nothing. Never when RAG is off. | `{query}` |

`system` and `no_context` are `optional`: an empty template turns them off
entirely rather than sending a blank message.

### Where each default came from

- **Context block** — was the `CONTEXT_INSTRUCTION` constant. Wording unchanged.
- **Chunk format** — was the inline `[Chunk {id} | score={score:.3f}]` f-string.
  Rendering unchanged.
- **System prompt** — new. There was none, so the model was given no grounding
  or citation rules at all.
- **Empty retrieval fallback** — new. Nothing was inserted, so an empty search
  produced an answer from general knowledge that read as though it were grounded.

---

## 3. Behaviour changes

These are real changes to what the model receives, not just a new screen.

- Answers read differently: tighter, more likely to refuse than improvise, and
  citing chunk ids.
- Roughly **115 extra input tokens per request** from the system prompt.
  Measured: a trivial ungrounded question went from ~15 to 131 input tokens.
- A question that retrieves nothing is now told so, rather than answered from
  general knowledge.
- **Traces record the system prompt actually in force**, not the one the client
  sent. With a configured default those stopped being the same thing, and only
  the first can be judged later.

To restore the previous behaviour exactly, blank the **System prompt** and the
**Empty retrieval fallback**. Nothing else changed.

### Precedence

A caller's own `system_prompt` on `POST /chat` still wins over the configured
one. `null` means "use what is configured"; `""` means "send none".

---

## 4. How a request is assembled

```
system   ← system prompt          (skipped when blank, or overridden by the caller)
system   ← context block          (chunks present)
         ↳ chunk format × N, joined by a blank line
   …or ← empty retrieval fallback (grounded, nothing retrieved)
   …or ← nothing                  (RAG off — nothing was searched)
user     ← the question
```

`grounded` is `use_rag or context_chunks`. It exists so the fallback never
reports a failure that did not happen: with RAG off there was nothing to search,
which is not the same as searching and finding nothing.

---

## 5. API

| Method | Path | Does |
|---|---|---|
| `GET` | `/prompts` | List every prompt, its default, and its variables. |
| `GET` | `/prompts/{id}` | One prompt as it stands. |
| `PUT` | `/prompts/{id}` | Save an override. `400` if it would not render, `404` on an unknown id. |
| `POST` | `/prompts/{id}/reset` | Restore the shipped default. Resetting an untouched prompt is not an error. |
| `POST` | `/prompts/preview` | Render the prompts in force into the messages they produce. |

Saving text identical to the shipped default is treated as a **reset**, so a
prompt stops reading as edited once it matches the code again.

### Validation

A template is checked before it is stored, so a bad edit fails where there is a
person to read the reason rather than mid-stream. Rejected:

- a placeholder the pipeline has no value for (`{similarity}`);
- a required placeholder dropped (`{chunks}`, `{content}`);
- unbalanced braces, or a positional field (`{}`);
- a format spec the value rejects (`{score:.2f}` — score arrives pre-rounded as
  a string, so a template writes a bare `{score}`);
- an empty template on a prompt that is not `optional`.

---

## 6. Storage

Overrides live in `backend/data/prompts.db` — a third SQLite file, alongside
`runs.db` and `traces.db`.

Its own file because its retention rule is **never**. Run history prunes at
thirty days and takes unjudged traces with it; the wording every answer is
written under must not share a database with anything that deletes on a timer.

A row exists only where a prompt has actually been overridden, which is what
makes a reset a delete — and what lets an untouched prompt keep following the
code.

### Caching

Held in memory between reads, and deliberately **not** in Redis — the opposite
call from the source endpoints, on the opposite evidence. Measured against this
deployment's Redis:

| | per read |
|---|---|
| Reading the four rows from SQLite | 0.057 ms |
| A Redis round trip | 0.103 ms — **1.8x slower** |
| The in-process memo | ~0 ms — 342x cheaper than the read |

Redis is the right tool for the source reads because those are network calls to
two other services. It is the wrong tool here, where it would replace a local
file read with a network hop and lose.

The memo is safe because of the writer count: `save` and `reset` are the only
things that touch the table, and both clear it. There is no console and no
second writer to miss, so the guarantee holds exactly — an edit applies to the
next question, with no TTL and no staleness window.

That is single-process reasoning. `prompt_cache_enabled=false` turns it off for
a deployment with more than one worker, where each would otherwise hold its own
copy and never hear about the others' edits.

---

## 7. Files

**New — backend**

| File | Holds |
|---|---|
| `app/schemas/prompt.py` | `Prompt`, `PromptId`, `PromptVariable`, update/preview payloads. |
| `app/services/prompt_catalog.py` | The shipped defaults, the variables, and template validation. |
| `app/services/prompt_db.py` | Connection and schema for `prompts.db`. |
| `app/services/prompt_store.py` | Which text is in force; list, save, reset, preview. |
| `app/routers/prompts.py` | The five endpoints. HTTP only. |
| `app/docs/prompts.py` | Their OpenAPI descriptions. |

**New — frontend**

| File | Holds |
|---|---|
| `src/hooks/usePrompts.ts` | Loads the prompts; `usePromptPreview` renders them. |
| `src/features/prompts/PromptsView.tsx` | The screen. |
| `src/features/prompts/PromptEditor.tsx` | One prompt, open for editing. |
| `src/features/prompts/AssembledPrompt.tsx` | The assembled-request panel. |

**Changed**

| File | Change |
|---|---|
| `app/services/prompt_builder.py` | Renders supplied templates instead of constants. Stays pure — it takes them rather than fetching them, so it is still callable with no database behind it. Adds `grounded`, `resolve_system_prompt`, `sample_chunks`. |
| `app/routers/chat.py` | Reads the prompts in force before the stream opens, resolves the effective system prompt, passes both to the builder. |
| `app/services/chat_trace.py` | Records the system prompt in force rather than the request's own field. |
| `app/config.py` | Adds `prompt_store_path`. |
| `app/main.py` | Registers the router; opens the store at startup and logs how many prompts are overridden. |
| `app/docs/__init__.py`, `app/schemas/__init__.py` | Re-exports. |
| `frontend/src/api/types.ts`, `client.ts` | Prompt types and the five calls. |
| `frontend/src/App.tsx` | The fourth tab. |

---

## 8. Verified

- 40 checks over the registry, store, validation, preview and persistence — all
  passing. Covers: order and contents of the list, every rejection above with
  its message, edit → effect → reset, saving-the-default-is-a-reset, blanking an
  optional prompt, and that only genuinely-overridden prompts are stored.
- Live against a running backend: an edit round-tripped, survived a restart, and
  appeared in the assembled request.
- A real `POST /chat` request confirmed the resolved system prompt reaches the
  model (131 input tokens) and lands in the trace.
- Frontend `tsc` clean, `oxlint` clean, production build clean.

---

## 9. Previously-known issue, now fixed

The backend would not start on the working tree: `.env` carried `REDIS_HOST`,
`REDIS_PORT` and `REDIS_PASSWORD`, which `config.py` rejected as extra inputs.
`config.py` now accepts the Redis connection either as those parts or as a
single `REDIS_URL`, so the backend starts against the real `.env`.
