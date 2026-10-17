# duckdb-vectors

A python package that facilitates the implementation of semantic search into any kind of agentic system, in 3 steps:

1. chunk documents and store their embeddings in a DuckDB database
2. bake the database into the container that packages your agent
3. search semantically using vector distances and BM25 in tens of milliseconds

## Use cases

`duckdb-vectors` is a nimble building block that has been designed for following use cases:

- __Retrieval-Augmented Generation (RAG) powered by thousands of static documents__ — You do not see the need for an external vector store or knowledge base. You prefer a composable toolchain over fat frameworks. You are looking for extremely fast responses. [Turn an existing markdown folder into a RAG agentic capability](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/setup-rag-procedure.md).

- __Select skills exposed to your agents__ — The toolbox of your agent is growing exponentially. Create a tool for selecting appropriate skills instead of exposing all of them. In addition, each tool comes with attributes that should be considered during the search.
  [Add a registry of hundreds of skills selected by semantic search](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/setup-skills-search-procedure.md).

- __Chat with your LLM-wiki__ — You have setup an agentic knowledge base that is managed and enhanced incrementally. It consists of a growing set of Markdown pages, that deserves careful chunking and embedding for response relevance. You want a simple complementary toolchain for chatting with your Wiki. [Leverage an LLM-managed wiki into an agent](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/setup-llm-wiki-to-rag-procedure.md).

## Quick start

Get `duckdb-vectors` from the PyPI repository:

```shell
$ pip install duckdb-vectors             # install once, batteries included
```

Index a set of Markdown pages into a single DuckDB file:

```shell
$ duckdb-vectors index ./docs            # index a folder of markdown
$ duckdb-vectors report                  # report on database content
$ duckdb-vectors search "my question"    # hybrid search
```

Then perform semantic search from within your python code:

```python
from duckdb_vectors import VectorStore

store = VectorStore()

for hit in store.query("how does an embedded database work?", top_k=3):
    print(hit.score, hit.chunk.chunk_id, hit.chunk.text)
```

## Customization

The default configuration of `duckdb-vectors` makes it workable out of the box. But you do have multiple options for adapting it to your specific requirements:

- Choose your harnessing framework, or create your own. `duckdb-vectors` focuses on semantic search. Pass a string, and it returns matching chunks. Use them as you want.

- Frontmatter headers of Markdown pages are injected as JSON objects. Each attribute can be considered in SELECT statements in addition to semantic search. Metadata is returned along with each chunk.

- Use a local embedding model, or a remote one. Titan from Amazon Bedrock is built-in. You can expect the registry of available embeddings to grow over time.

- Implement your own chunking class, that is fully adapted to your knowledge base. We have focused on Markdown files. You may have other kinds of documents to ingest.

## Why

You are right, trillions of solutions exist for semantic search. So, why bother?

Because we are frugal.

- We won't embrace a fat framework, but limit our code base and dependencies to the strict minimum.

- We won't delegate via the network a search across some thousands of items. A local function call is by far the fastest path to relevant answers.

- We won't use stemming search at all. Semantic search for sentences and exact matches for keywords cover our needs.

- We won't externalize a read-only vector database or knowledge store for some thousands of items. This should be baked into the file system of the agent itself.

- We won't introduce separate packaging for the code and for data, since modern agents rely on Docker images anyway.

- We won't invest on non-differentiating features. Trusted harnessing will come from [boring technologies](https://mcfunley.com/choose-boring-technology).

We did our homework, as reflected in the [Alternatives](#alternatives) section. It appears, in the end, that no python package was available to cover our frugal needs.

## Documentation

Start here and go deeper as you need:

- New to `duckdb-vectors`? Read the [primitive](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/duckdb-vectors-primitive.md)
  for the architecture and building blocks, then the
  [guide](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/duckdb-vectors-guide.md) for the end-to-end workflow.

- Building a specific capability? Follow a step-by-step procedure:
  [RAG answering machine](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/setup-rag-procedure.md),
  [LLM wiki to RAG](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/setup-llm-wiki-to-rag-procedure.md), or
  [semantic skills search](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/setup-skills-search-procedure.md).

- New to DuckDB? See the [DuckDB workbook](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/duckdb-workbook.md).

- Packaging an agent? See the [Docker agent guide](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/docker-agent-guide.md)
  and its [FAQ](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/docker-agent-workbook.md).

- Deploying on AWS? See the
  [Bedrock AgentCore guide](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/aws-bedrock-agentcore-guide.md) and
  [FAQ](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/aws-bedrock-agentcore-workbook.md), or the
  [Lambda guide](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/aws-lambda-guide.md) and
  [FAQ](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/aws-lambda-workbook.md).

- Contributing or developing? Start with [CONTRIBUTING.md](https://github.com/bernard357/duckdb-vectors/blob/main/CONTRIBUTING.md),
  then the [developer workbook](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/developer-workbook.md) for
  prerequisites, setup, and the full workflow.

## Configuration

Settings resolve with the precedence: explicit argument >
`DUCKDB_VECTORS_*` environment variable > default.

| Setting | Argument | Environment variable | Default |
| --- | --- | --- | --- |
| Database path | `db_path` | `DUCKDB_VECTORS_DB_PATH` | `vectors.duckdb` |
| Embedding backend | `backend` | `DUCKDB_VECTORS_BACKEND` | `local` |
| Chunker | `chunker` | `DUCKDB_VECTORS_CHUNKER` | `markdown` |
| Read-only | `read_only` | `DUCKDB_VECTORS_READ_ONLY` | `false` |

## Embedding backends

Backends are resolved from a string: a bare name (`local`) or a `name:variant`
pair (`bedrock:titan`). `local` is the default and the minimum viable setup for
end users. The registry is contributor-extensible — register open or closed,
local or cloud backends without touching the core:

```python
from duckdb_vectors import register_embedder

register_embedder("mybackend", lambda variant: MyEmbedder(variant))
```

| Backend | Spec | Dimensions | Use for |
| --- | --- | --- | --- |
| fastembed (ONNX, local) | `local` | 384 | default; real relevance, offline |
| Bedrock Titan | `bedrock:titan` | 1024 | cloud embedding |
| Hashing (deterministic, no deps) | `hashing` | 384 | tests and CI only, not relevance |

## Chunking backends

A chunker is resolved from a string: a bare name (`markdown`) or a
`name:variant` pair. `markdown` is the default and the minimum viable setup for
end users. The registry is contributor-extensible — register your own splitter
without touching the core:

```python
from duckdb_vectors import register_chunker

register_chunker("mychunker", lambda variant: MyChunker(variant))
```

| Chunker | Spec | Use for |
| --- | --- | --- |
| MarkdownChunker | `markdown` | default; heading-scoped sections with word-window splitting |

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
  [`examples/Dockerfile`](https://github.com/bernard357/duckdb-vectors/blob/main/examples/Dockerfile).
- __Runtime append__: a harness opens the store read-write at init and appends
  a registry of tools or skills. See
  [`examples/skill_registry.py`](https://github.com/bernard357/duckdb-vectors/blob/main/examples/skill_registry.py).

## Alternatives

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

## Contributing

Contributions are welcome — adding an embedding backend or a chunker is a great
first pull request. See [CONTRIBUTING.md](https://github.com/bernard357/duckdb-vectors/blob/main/CONTRIBUTING.md) for the development
setup, project principles, and pull-request guidelines.

## License

MIT.
