# Question: How does a Pinecone Index isolate embeddings?

## Answer
A Pinecone **Index** is an isolated vector space. All read and write operations go through a specific index, and data from one index is completely isolated from another.

### Pinecone Hierarchy
| Level         | What it is                                                                 | Analogy                          |
|---------------|---------------------------------------------------------------------------|----------------------------------|
| **Project**   | Your Pinecone account/project. Created when you sign up.                  | A database server                |
| **Index**     | A named, isolated vector space. Metric, dimensions, and region are locked. | A database table                 |
| **Namespace** | (Optional) A sub-partition inside an index. Queries never cross namespaces. | A schema/partition within a table |

### Key Points
- When you set `pinecone_index_name = "rag-index"`, all upserts and queries are scoped to that specific vector space.
- Nothing from another index can leak in or be queried.
- On the **free tier**, you get **one index**. Use **namespaces** to logically separate different document collections within that single index (e.g., `namespace="legal-docs"` vs `namespace="product-docs"`).
