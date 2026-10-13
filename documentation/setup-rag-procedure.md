# Setup RAG procedure

A step-by-step procedure to build an answering machine over your own documents
with `duckdb-vectors` — the "answering-machine-on-duckdb-vectors" used in the
guides. Follow the steps in order; each is a concrete action you can replay.

- [Step 1 — get chunkable documents](#step-1)
- [Step 2 — gather them in one place](#step-2)
- [Step 3 — chunk and index](#step-3)
- [Step 4 — integrate in local software](#step-4)
- [Step 5 — package a container with the database](#step-5)
- [Step 6 — release a container with the agent and the database](#step-6)

## Step 1 — get chunkable documents {#step-1}

`duckdb-vectors` chunks markdown first-class, because the default chunker splits
on headings. Convert other sources to markdown before indexing:

- Already markdown (docs, wikis, READMEs): use as-is.
- HTML or web pages: convert to markdown (for example with a reader/clipper).
- PDFs and scans: run OCR, then convert the text to markdown; keep headings so
  the chunker can split on them.
- Office documents: export or convert to markdown.

Aim for documents with clear headings and sections of roughly a few hundred
words — that granularity embeds well and keeps retrieved chunks focused.

## Step 2 — gather them in one place {#step-2}

Collect the markdown in a single folder that will be the index input:

```text
docs/
  getting-started.md
  architecture.md
  faq/billing.md
```

The source can be a local folder, an S3 prefix synced down, a git checkout, or
a wiki export. `duckdb-vectors` only needs files on disk when indexing runs.

## Step 3 — chunk and index {#step-3}

Index the folder into a DuckDB file. Choose a chunking strategy and an
embedding backend:

```shell
$ duckdb-vectors index ./docs --recursive --backend local
```

- __Chunking options.__ The default `MarkdownChunker` splits on headings and
  windows long sections to ~300 words. Supply a custom `Chunker` (any object
  with `chunk(text, source)`) for other formats or sizes.
- __Embedding options.__ `local` (fastembed, offline, 384 dims) is the default
  and the minimum viable setup for real relevance; `bedrock:titan` embeds
  through Bedrock. (`hashing` is a deterministic, dependency-free fixture for
  tests and CI, not for relevance.) Re-running the index only re-embeds changed
  chunks (ETag reuse) and prunes chunks for deleted files.

Confirm the result:

```shell
$ duckdb-vectors report
```

## Step 4 — integrate in local software {#step-4}

Use the Python API against the local `.duckdb` file. Open read-only for
serving, retrieve per request, and hand the chunks to your LLM:

```python
from duckdb_vectors import VectorStore

store = VectorStore(db_path="vectors.duckdb", backend="local", read_only=True)


def answer(question: str) -> str:
    hits = store.query(question, top_k=5)
    context = "\n\n".join(f"[{h.chunk.source}] {h.chunk.text}" for h in hits)
    # Pass `context` and `question` to your LLM call, then return its answer.
    return context
```

At this point you have a working RAG retrieval layer in local software.

## Step 5 — package a container with the database {#step-5}

Freeze the index into an image so the container is self-contained. Build the
index in a builder stage and copy only the frozen file into the runtime stage:

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install "duckdb-vectors[local,cli]"
COPY docs/ ./docs/
RUN duckdb-vectors index ./docs --recursive --backend local --db /build/vectors.duckdb

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN pip install "duckdb-vectors[local]"
COPY --from=builder /build/vectors.duckdb /app/vectors.duckdb
ENV DUCKDB_VECTORS_DB_PATH=/app/vectors.duckdb
ENV DUCKDB_VECTORS_READ_ONLY=true
```

The container now holds the database and answers read-only with no external
service. See the [Docker agent guide](docker-agent-guide.md) for a complete,
runnable version.

## Step 6 — release a container with the agent and the database {#step-6}

Add your agent code to the runtime stage so one image bundles the agentic code,
the `duckdb-vectors` dependency, and the baked-in database:

```dockerfile
COPY agent.py /app/agent.py
ENTRYPOINT ["python", "/app/agent.py"]
```

Build, tag, and push to your registry; deploy anywhere that runs containers.
For managed AWS runtimes, continue with the
[Bedrock AgentCore guide](aws-bedrock-agentcore-guide.md) (agentic) or the
[Lambda guide](aws-lambda-guide.md) (single-turn). The release is one
reproducible artifact: code, dependency, and index together.
