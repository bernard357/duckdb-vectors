# duckdb-vectors guide

This guide takes you by the hand through the full workflow, from deciding what
you want your harness to do, to a search that answers inside your agent. It
follows the natural order of work:

- [Objectives](#objectives)
- [Gather content](#gather)
- [Index content](#index)
- [Search](#search)
- [Integrate into a harness](#integrate)
- [Where to go next](#next)

If you want the architecture first, read the
[primitive](duckdb-vectors-primitive.md). For installation and a one-screen
quickstart, see the [README](../README.md).

## Objectives {#objectives}

Start by naming the capability you want to add. `duckdb-vectors` is a good fit
when all of these hold:

- The corpus is small-to-medium — a few thousand markdown pages, or a few
  hundred tools or skills.
- Retrieval must be fast and local — tens of milliseconds, no external vector
  database.
- The index is read-mostly — built ahead of time, served many times.

Typical objectives: answer questions over a documentation set, let an agent
select among many tools or skills by meaning, or turn a maintained wiki into an
answering machine. Each has a step-by-step procedure under
`setup-*-procedure.md`.

## Gather content {#gather}

Collect the documents you want searchable in one place. Markdown is the
first-class input because the default chunker understands headings; convert
other formats (HTML, PDF via OCR, docx) to markdown first.

```text
docs/
  getting-started.md
  architecture.md
  faq/
    billing.md
    limits.md
```

A folder on the local filesystem is the simplest source. The same folder can
come from an S3 sync, a git checkout, or a wiki export — `duckdb-vectors` only
needs files on disk at index time.

## Index content {#index}

Indexing chunks each document, embeds the chunks, and writes them to a DuckDB
file. The folder indexer embeds only what changed since the last run.

```python
from duckdb_vectors import VectorStore, index_folder

store = VectorStore(db_path="vectors.duckdb", backend="local")
report = index_folder(store, "docs", recursive=True)
print(report)  # files, chunks, embedded, reused, pruned
store.close()
```

Or from the shell:

```shell
$ duckdb-vectors index ./docs --recursive --backend local
```

Two choices matter here:

- __Chunking__ — the default `MarkdownChunker` splits on headings and windows
  long sections to about 300 words. Supply your own `Chunker` (any object with
  `chunk(text, source)`) for other formats or strategies.
- __Embedding__ — `local` (fastembed, offline, 384 dims) is the default;
  `hashing` needs no dependency and is ideal for tests; `bedrock:titan` embeds
  through Amazon Bedrock. The backend identity is folded into each chunk's
  ETag, so switching backends re-embeds everything correctly.

## Search {#search}

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

## Integrate into a harness {#integrate}

Inside an agent, retrieval becomes context. The pattern is: open the store
read-only once at startup, then call `query` per user turn and feed the chunks
to the model.

```python
from duckdb_vectors import VectorStore

store = VectorStore(db_path="vectors.duckdb", backend="local", read_only=True)

def retrieve_context(question: str, k: int = 5) -> str:
    hits = store.query(question, top_k=k)
    return "\n\n".join(f"[{h.chunk.source}] {h.chunk.text}" for h in hits)

# Hand retrieve_context(question) to your LLM call as grounding, then let the
# model answer. duckdb-vectors stops at retrieval; the harness owns generation.
```

This is framework-agnostic: the same store drops into a Docker agent, a Lambda
function, AWS Strands, Pydantic AI, or Bedrock AgentCore. The platform guides
show each path concretely.

## Where to go next {#next}

- [docker-agent-guide.md](docker-agent-guide.md) — package an answering agent
  in a container, built on this very documentation folder
- [aws-bedrock-agentcore-guide.md](aws-bedrock-agentcore-guide.md) — deploy
  that agent on Bedrock AgentCore via the CLI
- [aws-lambda-guide.md](aws-lambda-guide.md) — a query-in / answer-out Lambda
  with a direct Bedrock RAG call
- [setup-rag-procedure.md](setup-rag-procedure.md) — the end-to-end procedure
- [duckdb-vectors-primitive.md](duckdb-vectors-primitive.md) — the architecture
