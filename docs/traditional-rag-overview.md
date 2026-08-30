# Traditional RAG Pipeline Overview

This document provides a high-level reference for a basic Retrieval-Augmented Generation (RAG) pipeline.

## Reference File
The core text description can be found at: `data/00-traditional-rag.txt`

## Pipeline Flow
The pipeline is a sequential flow that takes raw data, processes it for search, retrieves context based on a query, and uses it to augment an LLM's response. 

The pipeline is split into three distinct sections:

**1. Data Phase:** `Data` -> `Chunking & Overlap` -> `Embedding` -> `Store in Vector DB`
**2. Retrieval Phase:** `User Query` -> `Embed Query` -> `Similarity Search`
**3. Generation Phase:** `Prompt Engineering` -> `Pass to LLM` -> `Generate Response`

This process ensures that the Large Language Model has contextually relevant, up-to-date information when answering queries, mitigating hallucinations.
