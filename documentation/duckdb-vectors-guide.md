# User guide

Is `duckdb-vectors` adapted to your use case? And how to use it? Let see how you can leverage the package for your agent.

If you are looking for an internal view of `duckdb-vectors`, then jump to the [Technical design](duckdb-vectors-primitive.md) page. And if you want to contribute, go to the [Developer workbook](developer-workbook.md).

## Is `duckdb-vectors` a good fit for your use case? {#evaluate}

In-process semantic search fits when the corpus is in the thousands of items, mostly static, and travels with a single agent, and when a local function call beating a network round trip matters more than elastic write throughput. It does __not__ replace a managed vector database for large multi-tenant corpora, high-churn write workloads, or sustained high-concurrency serving. Naming the boundary is part of the pitch: the smaller scope is exactly what buys the simplicity.

The mismatch is easiest to see side by side:

| Facet | Separate vector database | `duckdb-vectors` |
| --- | --- | --- |
| Corpus size | millions of vectors, sharded | a few thousand items, in one file |
| Write pattern | high-churn, live updates | read-mostly, re-indexed on build |
| Deployment | a separate service to run | a file baked into the agent image |
| Query path | MCP or network round trip | local call, in-process execution |
| Concurrency | scalable back-end service | scale-out by design |
| You operate | deploy, secure, monitor, pay | `pip install` and `Dockerfile` |

Pick the left column when you genuinely have that scale. For the right column — the in-process semantic search — the left column is machinery you do not need.

## How to install `duckdb-vectors`? {#install}

This package is exposed publicly from PyPI.

```shell
$ pip install duckdb-vectors             # install once, batteries included
```

As usual, you can also use `uv` for the installation, and you can also add `duckdb-vectors` to some `requirements.txt`.

The install brings the `duckdb-vectors` command. Run it with no arguments, or with `--help`, to list the verbs and their options:

```shell
$ duckdb-vectors --help          # list the index, report, and search verbs
$ duckdb-vectors index --help    # options for a specific verb
```

## How to gather content? {#gather}

Collect the documents you want searchable in one place. Markdown is the first-class input because the default chunker understands headings; convert other formats (HTML, PDF via OCR, docx) to markdown first.

```text
documentation/
  getting-started.md
  architecture.md
  faq/
    billing.md
    limits.md
```

A folder on the local filesystem is the simplest source. The same folder can come from an S3 sync, a git checkout, or a wiki export — `duckdb-vectors` needs files on disk at index time.

Enrich each markdown page with meta-attributes that you want to use at search time. A YAML frontmatter block at the top of a page is parsed and merged into every chunk of that page:

```markdown
---
kind: faq
product: billing
audience: customer
---

# Refund window

Refunds are processed within 14 days of the request.
```

Each key lands in `chunk.metadata`, is returned with every hit, and can be referenced from the ranking SQL (see [How to search](#search)). The chunker's own `section_path` is always preserved, so frontmatter keys never overwrite it.

## How to index content? {#index}

Indexing chunks each document, embeds the chunks, and writes them to a DuckDB file. The folder indexer embeds only what changed since the last run.

You can use either the CLI or the python interface.

Two choices matter for the indexation of the content:

- __Chunking__ — the default `MarkdownChunker` splits on headings and windows
  long sections to about 300 words. Supply your own `Chunker` (any object with
  `chunk(text, source)`) for other formats or strategies.

- __Embedding__ — `local` (fastembed, offline, 384 dims) is the default and the
  minimum viable setup for real relevance; `bedrock:titan` embeds through Amazon
  Bedrock. (`hashing` exists as a deterministic, dependency-free fixture for
  tests and CI — not for relevance.) The backend identity is folded into each
  chunk's ETag, so switching backends re-embeds everything correctly.

Here is an example of indexing with python:

```python
from duckdb_vectors import VectorStore, index_folder

store = VectorStore(db_path="vectors.duckdb", backend="local")
report = index_folder(store, "documentation", recursive=True)
print(report)  # files, chunks, embedded, reused, pruned
store.close()
```

`VectorStore` accepts these arguments:

- `db_path` — the DuckDB file (default `vectors.duckdb`).
- `backend` — the embedding backend spec, e.g. `local` or `bedrock:titan` (default `local`).
- `read_only` — open the file without writing; the serving default (default `false`).
- `embedder` — pass a ready `Embedder` instance to bypass backend resolution.
- `settings` — pass a fully-built `Settings` object instead of the individual arguments.

Each argument resolves with the precedence explicit argument > `DUCKDB_VECTORS_*` environment variable > default, so `DUCKDB_VECTORS_DB_PATH`, `DUCKDB_VECTORS_BACKEND`, and `DUCKDB_VECTORS_READ_ONLY` configure the same settings without code changes — handy in a container.

Or from the shell:

```shell
$ duckdb-vectors index ./documentation --recursive --backend local
```

The `index` verb takes `--db` (file path), `--backend` (embedding spec), `--recursive` (descend into sub-folders), and `--json` (emit the report as JSON instead of a human-readable line).

Check what landed in the database with the `report` verb — it prints the file path, backend, embedding dimensions, chunk count, and the indexed sources:

```shell
$ duckdb-vectors report                  # summary of database content
$ duckdb-vectors report --json           # same, as JSON
```

## How to search for matching content? {#search}

Query with a natural-language string. Search is hybrid by default: semantic
similarity plus exact-token BM25, fused with Reciprocal Rank Fusion.

```python
store = VectorStore(db_path="vectors.duckdb", backend="local", read_only=True)
for hit in store.query("how do retries work?", top_k=5):
    print(f"{hit.score:.4f}  {hit.chunk.source}  {hit.chunk.title}")
```

Restrict scope or decay by age with an injected `SELECT` — the ranking SQL
owns its own `WHERE` and `ORDER BY`:

```python
scoped = (
    "SELECT chunk_id FROM {table} "
    "WHERE json_extract_string(metadata, '$.kind') = 'faq' "
    "ORDER BY {distance} LIMIT {top_k}"
)
hits = store.query("refund window", vector_sql=scoped, text_sql=scoped)
```

For a quick check from the shell, the `search` verb runs the same hybrid query and prints the ranked hits:

```shell
$ duckdb-vectors search "how do retries work?" --top-k 5
$ duckdb-vectors search "how do retries work?" --json      # structured output
```

## How to integrate into a harness? {#integrate}

Inside an agent, retrieval becomes context. The pattern is: open the store at startup, then call `query` per user turn and feed the chunks to the model. This is framework-agnostic: the same store drops into a Docker agent, AWS Strands, Pydantic AI, etc. The examples below are basic snippets to be adapted to your own use case.

```python
from duckdb_vectors import VectorStore

store = VectorStore(db_path="vectors.duckdb", backend="local", read_only=True)

def retrieve_context(question: str, k: int = 5) -> str:
    hits = store.query(question, top_k=k)
    return "\n\n".join(f"[{h.chunk.source}] {h.chunk.text}" for h in hits)

# Hand retrieve_context(question) to your LLM call as grounding, then let the
# model answer. duckdb-vectors stops at retrieval; the harness owns generation.
```

The frontmatter you attached at index time rides back on every hit as `hit.chunk.metadata`, so the harness can label citations, filter by attribute, or route on it:

```python
def retrieve_context(question: str, k: int = 5) -> str:
    blocks = []
    for h in store.query(question, top_k=k):
        kind = h.chunk.metadata.get("kind", "doc")
        blocks.append(f"[{kind}: {h.chunk.source}] {h.chunk.text}")
    return "\n\n".join(blocks)
```

See the [Docker agent guide](docker-agent-guide.md) for building an agentic harness from a configuration file.

## How to build a container image? {#container}

Freeze the index into an image so the container is self-contained. Build the
index in a builder stage and copy only the frozen file into the runtime stage, then add the code of your agent:

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install duckdb-vectors
COPY documentation/ ./documentation/
RUN duckdb-vectors index ./documentation --recursive --backend local --db /build/vectors.duckdb

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN pip install duckdb-vectors
COPY --from=builder /build/vectors.duckdb /app/vectors.duckdb
ENV DUCKDB_VECTORS_DB_PATH=/app/vectors.duckdb
ENV DUCKDB_VECTORS_READ_ONLY=true

COPY agent.py /app/agent.py
ENTRYPOINT ["python", "/app/agent.py", "--db", "/app/vectors.duckdb"]
CMD ["what is duckdb-vectors?"]
```

Once the `Dockerfile` has been built, it contains both the database and the agent to query it in-process:

```shell
$ docker build -f examples/docker-agent/Dockerfile -t duckdb-vectors-agent .
$ docker run --rm duckdb-vectors-agent "how is a search answered?"
```

The frozen index is inside the image, so the container needs no volume and no network to answer.

## How to deploy? {#deploy}

Because the index is a file baked into the image, deployment is just "run the container" — there is no data tier to provision alongside it. The same artifact runs across targets:

- __Local / in-process__ — import `VectorStore` directly in a script or notebook, or `docker run` the image. No network, no service.

- __Containers (e.g., Kubernetes, ECS)__ — ship the image to any orchestrator. The container is stateless and read-only at serving time, so it scales horizontally by running more replicas; each replica reads its own baked index.

- __AWS Lambda__ — package the agentic code as a container image and deploy it as a Lambda function. The frozen index travels inside the image, so cold starts pay no data-loading or network cost. See the [AWS Lambda guide](aws-lambda-guide.md).

- __Amazon Bedrock AgentCore__ — run the container as an AgentCore runtime, letting AgentCore own the agent loop while `duckdb-vectors` serves retrieval in-process. See the [Bedrock AgentCore guide](aws-bedrock-agentcore-guide.md)
