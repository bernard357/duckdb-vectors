# AGENTS.md — adopting and developing duckdb-vectors

`duckdb-vectors` is a frugal, embeddable semantic-search building block for agentic systems: hybrid vector + exact-token search over a local DuckDB file, no external vector database. This file serves two audiences: an agent **adopting** the package in its own project (first half), and an agent or agentic IDE **working in this repository** (second half).

## Install for using the package

```shell
$ pip install duckdb-vectors             # batteries included (local embeddings)
$ pip install duckdb-vectors[bedrock]    # add the Amazon Titan embedder
```

## SDK entry point

Open a store against a local `.duckdb` file and query it. Open read-only in the serving path, when the index is a frozen artifact baked into the container:

```python
from duckdb_vectors import VectorStore

store = VectorStore(db_path="vectors.duckdb", backend="local", read_only=True)

hits = store.query("how does an embedded database work?", top_k=5)
for hit in hits:
    print(hit.score, hit.chunk.source, hit.chunk.title, hit.chunk.text)
```

Each hit exposes `hit.score` and a `hit.chunk` with `source`, `title`, and `text`.

## MCP tool

`duckdb-vectors mcp` is an MCP-over-stdio server exposing two read-only tools:

- `search(query, top_k=5)` — hybrid semantic + exact-token search; returns ranked chunks.
- `report()` — introspects the index: database path, backend, dimensions, chunk count, and sources.

An MCP client spawns `duckdb-vectors mcp` as a child process in the same container and talks to it over stdin/stdout; stdio is a same-container transport, not a network protocol.

## Setup for working on the package itself

Requirements:

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (Python env and builds)
- Node.js (VitePress docs and `markdownlint`) 
- `hadolint` lints the Dockerfiles

Shell commands:

```shell
$ make setup     # install Python + Node toolchain into a local venv
$ make test      # run the test suite
$ make lint      # ruff, mypy, markdownlint, hadolint
```

## The Makefile is the toolbox

Drive common tasks through `make`; do not call the underlying tools directly:

- `make` - list common targets
- `make setup` — check and install the development toolchain
- `make lint` / `make format` — check / apply ruff, mypy, markdownlint, hadolint.
- `make test` — run the pytest suite.
- `make build` — build the wheel and sdist (version from the git tag via hatch-vcs).
- `make build-documentation` / `make docs-serve` — build / preview the VitePress site.

## Project structure

```text
duckdb-vectors/
├── src/duckdb_vectors/   # the package
│   ├── store.py          # the VectorStore API
│   ├── indexer.py        # folder → DuckDB index
│   ├── cli.py            # the duckdb-vectors CLI (incl. the mcp verb)
│   ├── mcp_server.py     # the MCP-over-stdio server
│   ├── config.py         # Settings / DUCKDB_VECTORS_* env
│   ├── models.py         # Chunk / SearchResult pydantic models
│   ├── chunkers/         # pluggable chunkers (markdown default)
│   └── embedders/        # pluggable embedders (local, bedrock)
├── tests/                # pytest suite
├── examples/             # runnable container / Lambda / AgentCore agents
├── documentation/        # the VitePress site
├── fixtures/             # test and reference data
├── scripts/              # maintenance utilities
├── pyproject.toml        # metadata, deps, tag-driven version (hatch-vcs)
└── Makefile              # the task toolbox
```

Releases are tag-driven: the version comes from the git `vYYMMDD` tag, and pushing a tag publishes to PyPI.

## Pointers

- Documentation site: <https://bernard357.github.io/duckdb-vectors/>
- PyPI project: <https://pypi.org/project/duckdb-vectors/>
