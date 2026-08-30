# Question: Is Pinecone free?

## Answer
Pinecone is a commercial cloud service, but **yes, it has a very generous Free Tier (Starter Plan / Serverless)** that is excellent for personal projects and prototyping.

### What you get for free:
*   **One Index:** You are limited to running a single project/index at a time.
*   **Capacity:** Typically allows up to 100,000 vectors (which is more than enough for most personal document retrieval apps).
*   **No credit card required:** You can sign up and start using the free tier immediately.

### The Catch:
*   **It is not Open Source:** You cannot download Pinecone and host it on your own servers for free. It is strictly a managed cloud service.
*   **Inactivity Limits:** If you don't use your free index for a few days, Pinecone might automatically archive or delete it to save resources, requiring you to rebuild it.

If you want a 100% free, no-limits, run-anywhere solution, an open-source database like **ChromaDB** or **Qdrant (Local)** is a better choice. But if you just want to quickly build a cloud-based app, Pinecone's free tier is perfect.
