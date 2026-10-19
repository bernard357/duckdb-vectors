# AGENTS.md — adopting duckdb-vectors

`duckdb-vectors` is a frugal, embeddable semantic-search building block for agentic systems: hybrid vector + exact-token search over a local DuckDB file, no external vector database.

## Install

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

## Pointers

- Documentation site: <https://bernard357.github.io/duckdb-vectors/>
- PyPI project: <https://pypi.org/project/duckdb-vectors/>
