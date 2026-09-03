# Question: How does a Pinecone Index isolate embeddings, and when do I need a second one?

## Answer
A Pinecone **Index** is an isolated vector space, and a **Namespace** is a partition inside it. Both isolate reads — the difference is that an index is a physical resource with a cap on how many you get, while a namespace is free, unlimited in practice, and springs into existence on first write.

### Pinecone Hierarchy
| Level         | What it is                                                                 | Analogy                          |
|---------------|---------------------------------------------------------------------------|----------------------------------|
| **Project**   | Your Pinecone account/project. Created when you sign up.                  | A database server                |
| **Index**     | A named, isolated vector space. Metric, dimensions, and region are locked. | A database table                 |
| **Namespace** | (Optional) A sub-partition inside an index. Queries never cross namespaces. | A schema/partition within a table |

### Key points
- When you set `pinecone_index_name = "rag-index"`, all upserts and queries are scoped to that specific vector space.
- Nothing from another index can leak in or be queried.
- A query names **one** namespace and sees only that one. There is no "search every namespace" call.
- A query that names no namespace searches the **default** namespace (the empty-named one) — not everything. That is what this project's original production space was: `rag-index`, namespace `""`.
- A namespace is created by writing to it and ceases to exist once it is empty, so there is nothing to provision and nothing to clean up.
- **The same vector id can exist in every namespace.** `08723ac058830853#00003` lives in all four of this project's chunking namespaces and holds different text in each — an id names a *slot*, and only the namespace distinguishes them.

### Correcting an earlier note
This file used to say the free tier gives you **one** index. It does not: the Starter plan allows **up to 5 serverless indexes**. That mistake nearly cost this project a design — see [04-is-pinecone-free.md](04-is-pinecone-free.md), and check current limits against the docs rather than these notes.

---

## What genuinely forces a separate index

Only four things, and all of them are properties locked at index creation:

- **A different embedding model width.** Dimension is fixed per index, so comparing `text-embedding-3-small` (1536) against `-3-large` (3072) needs two indexes.
- **A different similarity metric.** Cosine, euclidean and dot product are chosen once and cannot be changed.
- **A different region**, for data-residency reasons.
- **A tenant large enough to need its own read capacity.** One index has shared capacity, so a customer with a hundred million vectors is a different shape of problem from a thousand small ones.

**Metadata shape is not on that list.** Metadata is per *record*: every vector carries its own dictionary of keys, and two vectors in the same namespace can have entirely different ones. A tenant who needs richer metadata needs no index of their own — the only real ceiling is **40 KB of filterable metadata per record**, which is that tenant's problem alone.

Two disciplines follow from metadata being schemaless rather than declared:

- Keep a field's type consistent wherever you filter on it. Nothing stops you storing `year` as a number for one tenant and a string for another, but a filter written for one shape silently fails to match the other.
- A key you never filter on costs nothing but storage, so per-tenant extras are cheap.

---

## What this project does with it

One index, `rag-chunk-lab`, with one namespace per chunking variant — `boundary-512-64`, `recursive-512-64`, `structural-512-64`, and so on.

Namespaces rather than an index per chunking strategy, because five indexes is a hard ceiling at four strategies plus production, and chunk size and overlap are variables worth sweeping alongside the strategy. Full reasoning in [docs/chunking-strategies.md](../docs/chunking-strategies.md) §3.

### Production stopped being an index

There used to be a second index, `rag-index`, holding what `/chat` answered from. Keeping it separate was about blast radius rather than isolation: a lab bug cannot write into an index it never opens.

It has been retired, because it made the wrong thing expensive. Deciding that one way of cutting the documents retrieves better should be a decision you can act on, and while production was a *place*, acting on it meant re-embedding the corpus into that place. Production is now a **pointer**: a stored variant id naming which namespace answers by default, moved in one call, reversible, with nothing copied.

The blast-radius guarantee is replaced by a narrower one: **a write must name its target.** `space_for` refuses an id it cannot parse rather than creating a namespace for it, and every read uses a handle that cannot provision — so asking about an index that no longer exists comes back empty rather than bringing it back.

That is also the honest trade. An index boundary is a stronger guarantee than a discipline, and this project gave one up for the other on purpose.

---

## The multitenancy case

Multitenancy is Pinecone's own first-named use for namespaces: one namespace per customer, with that customer's writes and queries targeted at theirs. If this app ever served more than one person, that is the pattern — and it is the same primitive already in use here for chunking variants.

What it buys:

- **Isolation with nothing to remember** — no per-query filter you can forget.
- **Offboarding in one call** — delete the namespace and that customer's vectors are gone.
- **Usage metering for free** — stats come back per namespace.
- **Cheap idle tenants** — serverless bills storage and reads, not namespaces.
- **Queries that do not slow down as you grow** — a query scans one namespace, so signing a new customer does not slow down an existing one's search.

The one that bites: **derive the namespace from the authenticated session, never from the request body.** If a client can send you a namespace string, your isolation is worth exactly what your input validation is worth. This project applies the same rule to variants — `chunk_variants.space_for` refuses an id it cannot parse rather than creating a namespace for it.

Scale limits are per plan: the docs say contact support above **100,000 namespaces**, and Standard/Enterprise accommodate million-scale for specific use cases.

And the alternative to reject: one shared namespace with a `tenant_id` metadata filter. It is the cheapest and the most dangerous — a single query that forgets the filter serves another customer's documents, and nothing fails visibly when it happens.

---

## Not confirmed

Older pod-based indexes let you declare which metadata fields were indexed at creation time, which *was* a genuine per-index metadata setting. Whether anything equivalent applies to serverless indexes is not confirmed here. It does not affect anything this project does; check it specifically before designing around it.
