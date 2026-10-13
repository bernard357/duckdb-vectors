# duckdb-vectors

Embeddable semantic-search building block for agentic systems: an
S3-Vectors-like interface over a local DuckDB database, with hybrid
vector + BM25 retrieval, pluggable embedding and chunking, and a CLI.

One DuckDB file is one index. Bake it into a container image and answer
semantic-search requests in tens of milliseconds — no external vector
database required.

## Why

Specialized agents commonly need semantic search over only a few thousand
markdown pages or a few hundred tools and skills, yet most solutions assume a
heavy external vector database. `duckdb-vectors` is a nimble, composable
component that does one thing well: efficient semantic search for common
agentic workloads. It starts with local embedding and evolves to cloud
embedding where needed, and stays framework-agnostic across Docker agents,
AWS Strands, Pydantic AI, and Bedrock AgentCore.

The bet is on boring technology — see
[Choose Boring Technology](https://mcfunley.com/choose-boring-technology).

## Install

```shell
$ pip install duckdb-vectors            # core (dependency-free hashing backend)
$ pip install duckdb-vectors[local]     # fastembed ONNX local embeddings
$ pip install duckdb-vectors[bedrock]   # Amazon Bedrock Titan embeddings
$ pip install duckdb-vectors[cli]       # the duckdb-vectors CLI
```

## Quickstart

```python
from duckdb_vectors import VectorStore

store = VectorStore(backend="local")  # or "hashing", "bedrock:titan"
store.add_texts(
    ids=["doc:0", "doc:1"],
    texts=[
        "DuckDB is an in-process analytical database.",
        "Vectors capture semantic similarity between texts.",
    ],
    metadatas=[{"source": "intro"}, {"source": "intro"}],
)
for hit in store.query("how does an embedded database work?", top_k=3):
    print(hit.score, hit.chunk.chunk_id, hit.chunk.text)
```

## CLI

```shell
$ duckdb-vectors index ./docs            # index a folder of markdown
$ duckdb-vectors report                  # report on database content
$ duckdb-vectors search "my question"    # hybrid search
```

## Configuration

Settings resolve with the precedence: explicit argument >
`DUCKDB_VECTORS_*` environment variable > default.

| Setting | Argument | Environment variable | Default |
| --- | --- | --- | --- |
| Database path | `db_path` | `DUCKDB_VECTORS_DB_PATH` | `vectors.duckdb` |
| Embedding backend | `backend` | `DUCKDB_VECTORS_BACKEND` | `local` |
| Read-only | `read_only` | `DUCKDB_VECTORS_READ_ONLY` | `false` |

## Embedding backends

Backends are resolved from a string: a bare name (`local`, `hashing`) or a
`name:variant` pair (`bedrock:titan`). The registry is contributor-extensible —
register open or closed, local or cloud backends without touching the core:

```python
from duckdb_vectors import register_embedder

register_embedder("mybackend", lambda variant: MyEmbedder(variant))
```

| Backend | Spec | Extra | Dimensions |
| --- | --- | --- | --- |
| Hashing (deterministic, no deps) | `hashing` | — | 384 |
| fastembed (ONNX, local) | `local` | `[local]` | 384 |
| Bedrock Titan | `bedrock:titan` | `[bedrock]` | 1024 |

## Metadata-aware search (injectable ranking SQL)

Each ranking signal — vector and BM25 — runs a DuckDB `SELECT` that returns
`chunk_id` best-first. The store collects the ids and fuses them with RRF. You
can override either SELECT to restrict in-scope chunks (a `WHERE` over
metadata) or to apply a decay factor on aging documents (a reshaped
`ORDER BY`). When you provide no override, built-in defaults reproduce the
standard behavior.

Templates use these substituted tokens: `{table}`, `{distance}` (vector only),
`{bm25}` (text only), `{top_k}`. The search text is available as the bound
parameter `$q`. Standard columns are in scope: `metadata`, `source`, `title`,
`stamped_at`, `text`.

Overrides can be set once (shared across queries) via `Settings` or
`DUCKDB_VECTORS_VECTOR_SQL` / `DUCKDB_VECTORS_TEXT_SQL`, or passed per call.
Precedence is per-call > settings > default. A custom SELECT owns its own
`WHERE` and supersedes the convenience `filter` dict for that signal.

Restrict to in-scope chunks:

```python
scoped = (
    "SELECT chunk_id FROM {table} "
    "WHERE json_extract_string(metadata, '$.kind') = 'policy' "
    "ORDER BY {distance} LIMIT {top_k}"
)
store.query("retention rules", vector_sql=scoped, text_sql=scoped)
```

Decay aging documents (older chunks sink; `age_days` lives in metadata):

```python
decayed = (
    "SELECT chunk_id FROM {table} "
    "ORDER BY {distance} "
    "* (1 + CAST(json_extract_string(metadata, '$.age_days') AS DOUBLE) "
    "/ 365.0) "
    "LIMIT {top_k}"
)
store.query("latest guidance", hybrid=False, vector_sql=decayed)
```

The injected SELECT is caller-authored SQL run against your own database
(opened read-only in the serving path); the query text and embedding are
parameter-bound.

## Lifecycles

- __Build-time indexing__: the CLI builds a `vectors.duckdb` at image build
  time; the container opens it read-only and never writes. See
  [`examples/Dockerfile`](examples/Dockerfile).
- __Runtime append__: a harness opens the store read-write at init and appends
  a registry of tools or skills. See
  [`examples/skill_registry.py`](examples/skill_registry.py).

## End-to-end use cases

- Leverage an LLM-managed wiki into an agent.
- Turn an existing markdown folder into a RAG agentic capability.
- Add a registry of hundreds of skills selected by semantic search.

## License

MIT.
