* Avoid adding the models name as co-author when pushing commits.
* Point out in the commit message milestone from the changes happened.
* Layer dependencies one way only: routers → services → schemas → config.
* Keep routers HTTP-only: validate, delegate, serialise — no business logic, no stray helper functions.
* Put anything a router would need as a helper in services/, one module per responsibility.
* Keep services framework-agnostic: no FastAPI imports, callable without an HTTP request.
* Define every request, response, and streamed event payload as a pydantic model in schemas/.
* Add an LLM provider as an adapter plus a factory registry entry, never an if-branch at a call site.
* Normalise vendor responses into our own schemas at the service boundary.
* Link a stored file to its vectors only through services/provenance.py — never spell out an id format or metadata key at a call site.
* Wrap synchronous SDK clients in asyncio.to_thread so they never block the event loop.
* Emit an error event and keep streaming when a non-essential stage fails mid-request.
* Type-hint every public signature and name constants at module top instead of inlining them.
* Keep OpenAPI docs in docs/, never inline in a route decorator.
* Adding or changing a streamed event means updating its schema and the endpoint's docs module in the same change.
* Never do long-running work inside a response: a request starts a job and returns its id, and progress is streamed from a separate endpoint that any client can reopen.
* Derive progress from what the stores already hold rather than a job table — the index is the durable record of what was embedded, so a run resumes instead of starting over.
* Log every pipeline stage and record every run in services/run_store.py; a run that leaves no trace cannot be debugged after the fact.
