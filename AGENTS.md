# AGENTS.md — developing duckdb-vectors

`duckdb-vectors` is a frugal, embeddable semantic-search building block for agentic systems: hybrid vector + exact-token search over a local DuckDB file, no external vector database. This file is for an agent or agentic IDE **working in this repository**.

For what the package is and how to **use** it — installation, the `VectorStore` SDK entry point, the `duckdb-vectors mcp` tool surface, the CLI verbs, and the embedding and chunking backends — see [README.md](README.md). Do not restate that material here; link to it so the two files never drift apart.

## The Makefile is the toolbox

Drive common tasks through `make`; do not call the underlying tools directly:

- `make` — list common targets
- `make setup` — check prerequisites and install the Python + Node toolchain into a local `.venv`
- `make lint` / `make format` — check / apply ruff, mypy, markdownlint, hadolint
- `make test` — run the pytest suite
- `make coverage` — run the suite under coverage
- `make build` — build the wheel and sdist (version from the git tag via hatch-vcs)
- `make build-documentation` / `make serve-documentation` — build / preview the VitePress site

Requirements: Python 3.12+ and [`uv`](https://docs.astral.sh/uv/); Node.js for the VitePress docs and `markdownlint`; `hadolint` for the Dockerfiles.

## For AI agents: how to implement changes

Prefer implementing single-file or single-module changes inline — edit the files directly against the existing `.venv`, run `make lint` and `make test` to verify, and commit. Reserve a full multi-step workflow (worktree, planner, implement-and-review loop) for multi-file or genuinely high-risk work (new architecture, cross-cutting refactors, schema or embedder/chunker-registry changes). The verification gate is non-negotiable either way: never claim a change works without running the lint/test gate, and for a behavioral change run an end-to-end check (index a folder, then `search`/`report`, or exercise the affected code path). When UI-facing or user-facing text changes, rebuild the docs with `make build-documentation`. Flag when a task looks borderline so a reviewed workflow can be chosen deliberately.

## House conventions

Conventions that are agent-facing and not already in README:

- **Flat `src/` layout.** The package lives in `src/duckdb_vectors/` (a `src` layout). Do not "fix" it to a nested or top-level-package layout.
- **Invoke venv binaries directly** — `.venv/bin/ruff`, `.venv/bin/pytest`, `.venv/bin/mypy` — never `source .venv/bin/activate`.
- **pydantic v2 `BaseModel` for data classes** — never `@dataclass`.
- **`X | None`, never `Optional[X]`.** 88-character lines; ruff + mypy clean.
- **Inject an optional boto3 `session` parameter** on every boto-dependent function or class (the Bedrock embedder), defaulting to a module-level or default session.
- **Prefer stdlib and the existing dependencies** — a new runtime dependency is a deliberate choice, recorded in `pyproject.toml`.
- **Tag-driven releases.** The version comes from the git `vYYMMDD` tag via hatch-vcs; there is no checked-in version string. Pushing a tag publishes to PyPI. Record changes in `CHANGES.md` under `## Unreleased`.

## Project structure

```text
duckdb-vectors/
├── src/duckdb_vectors/   # the package (src layout)
│   ├── store.py          # the VectorStore API (hybrid vector + BM25, RRF fusion)
│   ├── indexer.py        # folder → DuckDB index, with etag reuse
│   ├── cli.py            # the duckdb-vectors CLI (index / report / search / mcp)
│   ├── mcp_server.py     # the MCP-over-stdio server
│   ├── config.py         # Settings / DUCKDB_VECTORS_* env
│   ├── models.py         # Chunk / SearchResult pydantic models
│   ├── etag.py           # compute_etag content fingerprint
│   ├── py.typed          # ships type information
│   ├── chunkers/         # pluggable chunkers (markdown, pdf, composite default)
│   └── embedders/        # pluggable embedders (fastembed, hf, bedrock, hashing)
├── tests/                # pytest suite
├── documentation/        # the VitePress site
├── examples/             # runnable container / Lambda / AgentCore agents
├── skills/               # agent skills (e.g. search-duckdb-vectors)
├── fixtures/             # test and reference data
├── scripts/              # maintenance utilities
├── pyproject.toml        # metadata, deps, tag-driven version (hatch-vcs)
└── Makefile              # the task toolbox
```

The tree names the load-bearing modules an agent routinely edits, not every leaf, so routine additions do not falsify it.

## Pointers

- Documentation site: <https://bernard357.github.io/duckdb-vectors/>
- PyPI project: <https://pypi.org/project/duckdb-vectors/>
