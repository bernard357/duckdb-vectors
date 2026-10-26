# Turn an existing markdown folder into a RAG agentic capability

Here we build an answering machine over your own documents with [`duckdb-vectors`](https://pypi.org/project/duckdb-vectors/). Follow the steps in order; each is a concrete action you can replay.

## Step 1 — get chunkable documents

Indexing uses the composite chunker by default, dispatching each file to the
registered chunker claiming its MIME type. The markdown chunker is one
registered chunker: it splits markdown on headings. For formats no chunker
claims, convert to markdown first:

- Already markdown (documentation, wikis, READMEs): use as-is.
- HTML or web pages: convert to markdown (for example with a reader/clipper).
- PDFs and scans: handled directly by the PDF chunker (per-page text with a
  local OCR fallback), so no manual markdown conversion is required.
- Office documents: export or convert to markdown.

Aim for documents with clear headings and sections of roughly a few hundred
words — that granularity embeds well and keeps retrieved chunks focused.

## Step 2 — gather them in one place

Collect the markdown in a single folder that will be the index input:

```text
documentation/
  getting-started.md
  architecture.md
  faq/billing.md
```

The source can be a local folder, an S3 prefix synced down, a git checkout, or
a wiki export. `duckdb-vectors` only needs files on disk when indexing runs.

## Step 3 — chunk and index

Index the folder into a DuckDB file. Choose a chunking strategy and an
embedding backend:

```shell
$ duckdb-vectors index ./documentation --recursive --backend fastembed:minishlab/potion-multilingual-128M
```

- __Chunking options.__ Indexing dispatches each file through the composite
  chunker to the registered chunker claiming its MIME type; the `MarkdownChunker`
  splits markdown on headings and windows long sections to ~300 words, and the
  `PdfChunker` handles PDFs per page. Supply a custom `Chunker` (any object with
  `chunk(text, source)`) for other formats or sizes.
- __Embedding options.__ `fastembed:minishlab/potion-multilingual-128M`
  (offline, 256-dim static multilingual) is the default and the minimum viable
  setup for real relevance; other `fastembed:` tiers and `hf:<repo-id>` trade
  size for relevance, and `bedrock:titan` embeds through Bedrock. (`hashing` is a deterministic, dependency-free fixture for
  tests and CI, not for relevance.) Re-running the index only re-embeds changed
  chunks (ETag reuse) and prunes chunks for deleted files.

Confirm the result:

```shell
$ duckdb-vectors report
```

## Step 4 — integrate in agentic software

Use the Python API against the local `.duckdb` file. Open read-only for
serving, retrieve per request, and hand the chunks to your LLM:

```python
from duckdb_vectors import VectorStore

store = VectorStore(db_path="vectors.duckdb", backend="fastembed:minishlab/potion-multilingual-128M", read_only=True)


def answer(question: str) -> str:
    hits = store.query(question, top_k=5)
    context = "\n\n".join(f"[{h.chunk.source}] {h.chunk.text}" for h in hits)
    # Pass `context` and `question` to your LLM call, then return its answer.
    return context
```

At this point you have a working RAG retrieval layer in python.

## Step 5 — package a container with the database

Freeze the index into an image so the container is self-contained. Build the
index in a builder stage and copy only the frozen file into the runtime stage:

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install "duckdb-vectors"
COPY documentation/ ./documentation/
RUN duckdb-vectors index ./documentation --recursive --backend fastembed:minishlab/potion-multilingual-128M --db /build/vectors.duckdb

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN pip install "duckdb-vectors"
COPY --from=builder /build/vectors.duckdb /app/vectors.duckdb
ENV DUCKDB_VECTORS_DB_PATH=/app/vectors.duckdb
ENV DUCKDB_VECTORS_READ_ONLY=true
```

The container now holds the database and answers read-only with no external
service. See the [containerized agent guide](containerized-agent-guide.md) for a complete,
runnable version.

## Step 6 — release a container with the agent and the database

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
