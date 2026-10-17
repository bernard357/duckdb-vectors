# Changes

## Pending

### Added

### Changed

### Fixed

### Removed

## v261017

Make the published package match the README: a batteries-included install
(local embedding and the CLI work with no extras), Markdown frontmatter now
flows into chunk metadata, and chunkers gain a registry mirroring the one for
embedders. The CI pipeline now runs the same `make` targets developers use.

### Added

- chunker registry (`register_chunker`/`resolve_chunker`) selectable by string
  or `DUCKDB_VECTORS_CHUNKER`, mirroring the embedder registry (#11)
- parse Markdown YAML frontmatter into every chunk's metadata, preserving the
  reserved `section_path` and failing safe on malformed blocks (#10)

### Changed

- batteries-included install: `fastembed`, `typer`, and `pyyaml` are core
  dependencies, so a bare `pip install duckdb-vectors` runs the CLI and the
  local backend; `bedrock` stays an optional extra (#10)
- restructure the README around use cases, resolve review TODOs, and use
  absolute GitHub links so they render on PyPI (#1)
- run `make setup`/`lint`/`test` in CI to match the local toolchain, and bump
  the Node 24 action majors (#5)

### Metrics

- tests: 52 passed, 1 skipped
- python: 2725 lines (38 files)
- markdown: 3998 lines (36 files)

## v261014

First public release of duckdb-vectors: an embeddable, S3-Vectors-flavored
semantic-search building block over a local DuckDB file, with hybrid vector
plus exact-token BM25 retrieval, pluggable embedding and chunking, a CLI, and
end-to-end container lifecycles. Versioning is now derived from git tags.

### Added

- VectorStore over a single DuckDB file: S3-flavored put/query plus
  chunk-first add/query, per-chunk ETag, dual read-write/read-only lifecycle (#1)
- hybrid retrieval: brute-force cosine distance fused with exact-token BM25
  via reciprocal rank fusion, hybrid by default (#1)
- pluggable embedders (local fastembed default, bedrock Titan, deterministic
  hashing test fixture) and a swappable markdown chunker (#1)
- CLI to index a folder, report, and search (#1)
- metadata-aware search via injectable ranking SQL with built-in defaults (#2)
- comprehensive documentation set, contributor onboarding, and an indexed
  developer workbook (#3, #4, #6)
- lean Makefile covering setup, lint, test, build, and ping targets (#5)
- end-to-end ping lifecycles for Docker, Lambda, and AgentCore driven from
  make targets and shell snippets (#7)
- mypy type checking wired into the lint stage (#8)

### Changed

- derive the package version from git tags via hatch-vcs instead of a
  hardcoded string
- build the BM25 index without stemming or stopwords for exact-token and
  acronym matching
- lead the docs and examples with the local backend, reframing hashing as a
  test-only fixture (#4)
- lint Markdown and Dockerfiles alongside Python; make hadolint setup
  platform-neutral (#5)
- make setup non-interactive and venv-isolated (#5)
- assemble the AgentCore agent in a *.out build dir to keep the committed
  example pristine (#7)
- polish PyPI packaging metadata (#1)

### Removed

- drop the hardcoded `__version__` constant from the package in favour of
  tag-derived versioning
- drop the personal AWS profile name from the ping snippets (#7)

### Metrics

- tests: 39 passed, 1 skipped
- python: 2379 lines (34 files)
- markdown: 3940 lines (34 files)
