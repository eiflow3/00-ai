# Question: Is Pinecone's default similarity metric cosine?

## Answer
**Yes.** When you create a new Pinecone index, the default distance metric is **cosine similarity**.

Pinecone supports three metrics (chosen at index creation time and cannot be changed after):

| Metric         | Default? | Best For                                                                 |
|----------------|----------|--------------------------------------------------------------------------|
| **Cosine**     | ✅ Yes    | Text/semantic search (most common for RAG). Measures angle between vectors, ignoring magnitude. |
| **Euclidean**  | No       | When absolute distance matters (e.g., image features).                   |
| **Dot Product** | No       | When vectors are already normalized, or direction + magnitude both matter. |

For a RAG pipeline using text embeddings (like OpenAI's `text-embedding-3-small`), cosine is the correct choice — no changes needed.
