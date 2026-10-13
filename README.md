# duckdb-vectors

Embeddable semantic-search building block for agentic systems: an
S3-Vectors-like interface over a local DuckDB database, with hybrid
vector + exact-token BM25 retrieval, pluggable embedding and chunking,
and a CLI.

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

## Documentation

Start here and go deeper as you need:

- New to the component? Read the [primitive](documentation/duckdb-vectors-primitive.md)
  for the architecture and building blocks, then the
  [guide](documentation/duckdb-vectors-guide.md) for the end-to-end workflow.
- Working with DuckDB directly? See the [DuckDB workbook](documentation/duckdb-workbook.md).
- Packaging an agent? See the [Docker agent guide](documentation/docker-agent-guide.md)
  and its [FAQ](documentation/docker-agent-workbook.md).
- Deploying on AWS? See the
  [Bedrock AgentCore guide](documentation/aws-bedrock-agentcore-guide.md) and
  [FAQ](documentation/aws-bedrock-agentcore-workbook.md), or the
  [Lambda guide](documentation/aws-lambda-guide.md) and
  [FAQ](documentation/aws-lambda-workbook.md).
- Building a specific capability? Follow a step-by-step procedure:
  [RAG answering machine](documentation/setup-rag-procedure.md),
  [LLM wiki to RAG](documentation/setup-llm-wiki-to-rag-procedure.md), or
  [semantic skills search](documentation/setup-skills-search-procedure.md).

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

## Design note: lexical search without stemming

Hybrid search here fuses semantic (vector) retrieval with a BM25 lexical
signal, but the BM25 index is built deliberately with `stemmer = 'none'` and
`stopwords = 'none'`. This is a considered choice, not an oversight.

Semantic search already covers meaning, paraphrase, and synonyms, and it does
so across languages. Traditional stemmed full-text search (Porter stemming
plus a per-language stopword list) adds compute and storage, ties the index to
one language, and — in our prior benchmarks on comparable corpora — did not
improve relevance enough to justify that cost once semantic retrieval was in
place.

What vectors do *not* handle well is exact short tokens: acronyms, product
codes, identifiers, and single-keyword queries. That is precisely where a
lexical signal earns its keep. So the BM25 index is kept, but as a
language-agnostic exact-token match: `ESRS` finds `ESRS`, while `run` does not
conflate with `running`. The two signals are complementary — vectors for
meaning, exact-token BM25 for the literal strings vectors miss.

A developer who wants classic stemmed full-text behavior can still have it:
build their own FTS index and supply a custom `text_sql` (see above). The
default simply does not impose stemming on everyone.

## Lifecycles

- __Build-time indexing__: the CLI builds a `vectors.duckdb` at image build
  time; the container opens it read-only and never writes. See
  [`examples/Dockerfile`](examples/Dockerfile).
- __Runtime append__: a harness opens the store read-write at init and appends
  a registry of tools or skills. See
  [`examples/skill_registry.py`](examples/skill_registry.py).

## End-to-end use cases

Each use case has a step-by-step procedure under `documentation/`:

- Turn an existing markdown folder into a RAG agentic capability —
  [setup-rag-procedure.md](documentation/setup-rag-procedure.md).
- Leverage an LLM-managed wiki into an agent —
  [setup-llm-wiki-to-rag-procedure.md](documentation/setup-llm-wiki-to-rag-procedure.md).
- Add a registry of hundreds of skills selected by semantic search —
  [setup-skills-search-procedure.md](documentation/setup-skills-search-procedure.md).

## Related work and alternatives

`duckdb-vectors` is not the only way to put vectors in DuckDB. The projects
below overlap with it; each is a reasonable choice if its shape fits your
need better than ours. Our goal is narrow on purpose: a small, framework-
agnostic, read-mostly utility that bakes an index into a container image and
answers in tens of milliseconds, with pluggable embedding and chunking and an
S3-Vectors-flavored surface. Where a project pulls in a framework, a service,
or a broader scope, we point you to it rather than compete with it.

- [llama-index-vector-stores-duckdb](https://pypi.org/project/llama-index-vector-stores-duckdb/)
  — the official LlamaIndex integration that stores embeddings in DuckDB. Great
  if you are already building on LlamaIndex, because it plugs into that data
  model, retrievers, and query engines. We stay framework-agnostic: no
  LlamaIndex `Node`/`Document` types to adopt and no framework runtime to carry
  into the container.
- [vectorwrap](https://pypi.org/project/vectorwrap/)
  — a universal wrapper that switches one vector API across Postgres, MySQL,
  SQLite, DuckDB, and ClickHouse. Pick it when backend portability is the
  point. We go the opposite way: one backend (DuckDB), chosen deliberately, so
  we can lean on DuckDB-specific features (ARRAY columns, injectable ranking
  SQL) instead of a lowest-common-denominator API. It also expects you to bring
  your own embedding function; we ship pluggable embedders and markdown
  chunking.
- [microrag](https://pypi.org/project/microrag/)
  — the closest neighbour: a universal RAG library with ONNX-backed embeddings
  and DuckDB storage. If you want an end-to-end RAG library, it is a strong
  fit. We are not a RAG library — we deliberately stop at retrieval (store,
  index, search) and leave generation, prompting, and LLM orchestration to the
  harness, so the component composes into any framework rather than owning the
  pipeline.
- [duckdb `ai` community extension](https://duckdb.org/community_extensions/extensions/ai.html)
  — an extension that calls embedding and chat providers from inside SQL. Reach
  for it when you want embeddings generated in the database and don't mind the
  provider wiring living in SQL. We keep embedding in Python behind a small
  registry (local fastembed by default, cloud on demand), so the same code runs
  offline in CI and against a cloud provider in production without SQL-level
  credentials.
- [sqlrooms-rag](https://pypi.org/project/sqlrooms-rag/)
  — prepares and queries DuckDB-stored embeddings for RAG, primarily for the
  SQLRooms framework (usable standalone too). A good fit inside that ecosystem;
  outside it, our standalone, dependency-light surface and container lifecycle
  are a closer match.
- [raghilda](https://posit-dev.github.io/raghilda/)
  — a DuckDB-backed RAG store (from Posit) that can run fully in memory and
  search with BM25 when no embedding model is configured. Pleasant for quick
  local experiments. Our focus is the frozen, read-only, image-baked serving
  path and an S3-Vectors-flavored API rather than an in-memory exploration
  store.
- [dvs](https://github.com/allen2c/dvs)
  — a DuckDB + OpenAI vector similarity search service. Choose it if an
  OpenAI-tied service is what you want. We default to local, offline embeddings
  and treat cloud providers as an opt-in, keeping the base install slim and
  CI-friendly.

Note: a package named `rag007` was requested for this comparison but we could
not locate a matching project on PyPI or GitHub at review time; if you meant a
different name, open an issue and we will add it.

## Contributing

Contributions are welcome — adding an embedding backend or a chunker is a great
first pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development
setup, project principles, and pull-request guidelines.

## License

MIT.
