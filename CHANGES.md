# Changelog

All notable changes to `duckdb-vectors` are recorded here. Versions are
tag-driven (`vYYMMDD`); pushing a tag publishes the release to PyPI.

## Unreleased

### Added

- __`scripts/benchmark_indexing.py`.__ Measures a first index, an unchanged
  re-index, and a one-file edit, with the time the database file stays locked,
  on a synthetic Markdown folder generated from a fixed seed or on a real one
  with `--folder` (#59).

### Changed

- __Performance workbook.__ `documentation/performance-guide.md` becomes
  `documentation/performance-workbook.md` (URL `/performance-workbook`), a list
  of questions answered with measured figures first: index size, query time
  and its growth now measured to 100,000 chunks (~105 ms p50) instead of
  extrapolated, first-index and restart times on a real 247-file corpus (~3.6 s
  and ~1 s), search through the MCP server, sharing one index across sessions,
  writing from code, and model choice. The stale ~80% scan share is gone (#59).
- __`scripts/benchmark_performance.py` renamed `scripts/benchmark_queries.py`.__
  It no longer passes the removed `read_only` argument, which made it fail, and
  its synthetic chunks and queries use letter-only words so the keyword index
  holds real tokens; the full-text tokenizer drops digits (#59).
- __"How did these numbers change?" in the performance workbook.__ A table of
  the before and after figures behind each performance change (write-free
  re-index, pooled Markdown chunker, shared index, model kept loaded in the
  MCP server, deferred keyword-index rebuild, batched writes, 256-dim default),
  each pointing at the release that records it, plus a note on the rebuild
  projection that measurement proved wrong (#62).

- __Batched vector writes.__ `put_vectors` (and so `add_texts`) and
  `index_folder` now write each batch with one `DELETE ... IN` and one
  `INSERT ... SELECT unnest(...)` per 5,000 rows instead of two statements per
  chunk, inside one transaction, so a failed batch leaves no partial rows. An
  id repeated within one call keeps its last occurrence, as before, and stored
  `stamped_at` values are unchanged. On an M1 (2021): 50,000 chunks written in
  2,000-chunk `add_texts` batches went from 93 s to 19.7 s, and a first
  `index_folder` of a 247-file Markdown corpus (5,172 chunks) from 17.7 s to
  3.6 s with the default model (#61).

- __Lazy, verified keyword indexing.__ `put_vectors`, `add_texts` and
  `delete_vectors` no longer rebuild the full-text index on every call. A new
  `index_state` table in the database file records whether keyword indexing is
  current: writes mark it stale before the first row changes, and the index is
  rebuilt once, at `close()` or on the first keyword search that finds it
  stale, in any process sharing the file. A row written after the last rebuild
  used to be invisible to keyword search without any error if a writer crashed
  mid-batch; the next search now repairs it. Databases without the table count
  as current; a store with rows but no full-text index builds one. Writing in
  2,000-chunk batches is 10–20% faster (50,000 chunks: 110 s to 93 s); the
  rebuild itself is cheap (0.09 s for 50,000 chunks), and per-row writes
  remain the dominant cost (#60).

- __Documentation.__ README and the docs home page now list the Kiro knowledge
  base next to Kilo Code and link its procedure, and "read-only tool" wording
  is gone from those pages, the user guide, the six harness guides, and the
  Kilo procedure: the tools never change the indexed documents, but opening the
  index can create the database file (#58).

## v261103

Fast repeated searches through the MCP server, and a procedure for a shared
knowledge base across every Kiro workspace.

### Added

- __Kiro knowledge base procedure.__ `setup-kiro-knowledge-base-procedure.md`
  shows how to serve one folder to every Kiro IDE and Kiro CLI workspace: a
  user-level `mcp.json` entry launching `uvx duckdb-vectors mcp --index`, plus
  a global skill that routes house-practice questions to its tools (#55).

### Changed

- __MCP searches keep the embedding model loaded.__ The `mcp` server built a
  new embedder for every tool call, so each `search` reloaded the model. It
  now shares one embedder for its lifetime (also used by `--index` at
  startup), loading the model on the first search under a lock so concurrent
  first calls load it once; each call still opens and closes its own store, so
  sessions keep sharing one index. On the 5,172-chunk engineering-brain index,
  later searches went from about 1.25 s to 45 ms (median); `hf:` and other
  backends that probe their dimension no longer reload on every `report`
  (#57).

## v261102

A one-shot search skill for agents, a single machine-readable output contract
across the CLI, one index safely shared by concurrent sessions, and DuckDB 2.0
as the tested baseline.

### Breaking

- __DuckDB 2.0 baseline.__ The dependency moves from `duckdb>=1.1` to
  `duckdb>=2.0,<3`. Fresh installs were already resolving DuckDB 2.0.0; the
  range now matches what is tested and keeps a future 3.0 from arriving
  untested. DuckDB 2.0 opens databases written by 1.x, but files written under
  2.x cannot be read by DuckDB 1.x, so upgrade every process that shares a
  database file.

- __`--json` removed; `--output {json,yaml}` added.__ The `--json` flag is gone
  from every verb. `index`, `report`, and `search` now take `--output` with
  `json` (the default) or `yaml`, and the human-readable text branches are
  removed — structured output is the standard for every invocation. Scripts
  passing `--json` must drop it (JSON is already the default); pass
  `--output yaml` for YAML (#51).

### Added

- __`search-duckdb-vectors` skill.__ A `SKILL.md` under `skills/` that lets an
  agent drive the package directly — index a folder and run hybrid semantic +
  keyword search over `uvx`, or launch `duckdb-vectors mcp` in the agent's
  process space — with a self-selecting description so the agent can identify
  and leverage it (#51).
- __Self-describing MCP tools and server.__ The `search` and `report` tool
  descriptions are rewritten so an agent can decide when and how to call them,
  and `search` now returns each chunk's `metadata` alongside `chunk_id`,
  `text`, `source`, `title`, and `score`. The server itself now carries a
  `description`, an `instructions` block (scope with `report`, ground with
  `search`; documents never changed; JSON), and the installed package
  `version`, so an MCP client gets whole-server framing at the `initialize`
  handshake rather than only the per-tool descriptions. Each tool docstring
  now documents every returned field and where it comes from (#50).
- __Concurrent sessions can share one database file.__ DuckDB lets one process
  hold a file at a time and used to fail instantly when another held it, so two
  MCP servers on one index split 15/0 on queries and every query failed during
  another session's startup re-index. `VectorStore` now connects lazily, on
  first use, and retries a lock conflict with a short backoff for up to
  `lock_timeout` seconds (`DUCKDB_VECTORS_LOCK_TIMEOUT`, default 10) before
  raising the new `DatabaseLockedError`. Other I/O errors are not retried (#56).
- __HOL-Guard MCP contribution inputs.__ `fixtures/hol-guard/` stages the
  `tools/list` inventory and the reviewed per-tool policy (`review` for both
  tools, file-write and network risk disclosed) behind the HOL-Guard profile
  submitted upstream (#53).

### Changed

- __Re-indexing an unchanged folder writes nothing.__ The indexer now writes
  only chunks that are new or differ from what is stored (etag, source,
  position, title, or metadata), prunes by `chunk_id` instead of restamping
  every row, rebuilds full-text search only when rows changed, and releases the
  file while embedding. On a 247-file corpus (5,172 chunks) a no-op re-index
  went from 16–18 s holding the file throughout to about 1 s (with the pooled
  markdown chunker below) holding it for 0.02 s, and queries from another
  process during it went from 0/10 to 25/25. `IndexReport` gains a `written`
  count, also shown in the `mcp --index` startup log. A row's `stamped_at` now
  records when it was last written, not when it was last seen.
- __Markdown chunking is about 8× faster.__ `MarkdownChunker` used to open a
  DuckDB connection and load the `markdown` extension for every file, about
  85% of its time. It now keeps one connection for a whole indexing run and
  closes it in `end_indexing`: 247 files went from 7.8 s to 0.9 s, with
  identical chunks.
- __Honest MCP server instructions.__ The `initialize` instructions no longer
  call the tools read-only: they state that the tools never change the indexed
  documents, that opening the index can create an empty database file, and
  that `search` embeds the query with the configured backend (local by
  default).
- __Documentation.__ `AGENTS.md` now covers only agentic instructions and
  points to the README for usage (#52); README and the docs index drop the
  stale "opened read-only" wording left after the read-only removal, and the
  README links the Boring Steps blog post (#54).

## v261026

A zero-setup MCP deployment (auto-index on startup, `uvx` launch), the removal
of the read-only store mode, a dead-code sweep, and cross-surface navigation
across the README, docs site, and PyPI.

### Breaking

- __Read-only store mode removed.__ The `read_only` field on `Settings`, the
  `DUCKDB_VECTORS_READ_ONLY` environment variable, the `read_only` argument on
  `VectorStore`, and the `ReadOnlyError` class are all gone. A `VectorStore`
  now always opens writable and ensures its schema — one store mode for both
  reading and writing. The flag had no defined use case and blocked
  write-on-startup patterns (indexing and incremental re-indexing); callers
  passing `read_only=...` or catching `ReadOnlyError` must drop it.

### Added

- __Auto-index on MCP startup.__ `duckdb-vectors mcp --index <folder>` (with
  optional `--recursive`) indexes the folder into the database before serving,
  so an MCP deployment needs no separate indexing step. Indexing is incremental,
  so an unchanged folder is near-instant on restart; the index report is written
  to stderr, keeping stdout clean for the MCP protocol.
- __`uvx` launch for the editor use case.__ The Kilo Code knowledge-base
  procedure now launches the server with `uvx duckdb-vectors mcp --index …`, so
  an end-user needs neither a manual `pip install` nor a pre-built index.

### Changed

- __Cross-surface navigation.__ Every CLI/install documentation page now links
  PyPI inline at the point of use; the docs site gained a per-page "Edit on
  GitHub" link; the Kilo Code knowledge base is surfaced as a fourth use case.
- __Dead-code sweep.__ Added an advisory `make prune` (vulture) and removed the
  unused `DEFAULT_MODEL_ID` constant.

## v261025

A general Hugging Face embedding loader, composite-by-default chunking with
MIME-type matching, a measured performance guide spanning the embedding ladder
plus Qwen and EmbeddingGemma, a `make coverage` target, and a CLI `--version`
flag.

### Breaking

- __`CHUNKER` env var and `chunker` setting removed.__ The
  `DUCKDB_VECTORS_CHUNKER` environment variable and the `chunker` field on
  `Settings` are gone, together with the single-chunker selector. Indexing is
  now composite by default: `index_folder` dispatches each file to the
  registered chunker that claims its MIME type.
- __Chunkers claim files by MIME type instead of file suffix.__ A chunker
  declares a `mimetypes` frozenset; `FilesystemChunker.handles` resolves a
  path's MIME type via the stdlib `mimetypes.guess_type` and tests membership,
  so `.md`, `.MD`, and `.markdown` all map to `text/markdown`.
- The explicit `chunker=` argument on `index_folder` is retained as a
  code-level override for indexing a single format.

### Added

- __General Hugging Face ONNX loader.__ The `hf:<repo-id>` backend now loads the
  large majority of ONNX embedding exports, across four graph shapes handled
  additively: simple encoders, external-data ONNX (co-locating the sibling
  `model.onnx_data`), decoder-style graphs (synthesizing `position_ids` and
  feeding an empty KV-cache, e.g. Qwen3-Embedding), and full-pipeline exports
  that expose a baked-in `sentence_embedding` output (e.g. EmbeddingGemma),
  which the loader prefers over re-pooling the backbone.
- __`make coverage`__ runs the suite under `pytest-cov` and writes an HTML
  report; __`make serve-coverage`__ serves it over a local HTTP server (file
  pages need an HTTP origin). `coverage` is non-blocking for CI and suggests
  `serve-coverage` for interactive viewing.
- __CLI `--version`__ prints the tag-derived package version and exits.
- __Measured performance guide.__ A re-measured retrieval headline at the
  256-dim default (query p50 ~27 ms, embed ~0.3 ms, ~4.5 MB per 1,000 chunks on
  the reference machine), the embedding-model ladder with per-model latency and
  relevance, and measured rows for `hf:onnx-community/Qwen3-Embedding-0.6B-ONNX`
  (1024-dim) and `hf:onnx-community/embeddinggemma-300m-ONNX` (768-dim).
- An opt-in `model` pytest marker (deselected by `make test`) for real-model
  download-and-embed integration tests.

### Changed

- __Entry-point docs state Markdown and PDF indexing clearly__, and describe the
  composite chunker and MIME-type dispatch, replacing the former markdown-only
  framing.
- __Default embedding stays `fastembed:minishlab/potion-multilingual-128M`__
  (256-dim); README and `index.md` now reflect the 256-dim `FLOAT[256]` column
  and the re-measured latency rather than the previous 384-dim figures.

## v261024

A large feature release: measured performance guidance, a reworked embedding
backend surface, a filesystem-aware chunker contract, PDF ingestion with local
OCR, and a composite chunker for mixed-type folders.

### Breaking

- __Embedding backend selectors renamed.__ The `local` backend is removed with
  no alias; use `fastembed:<model>` for the bundled ONNX models and the new
  `hf:<repo-id>` loader for any Hugging Face ONNX model. The `model_identifier`
  etag prefix changes accordingly, so existing `vectors.duckdb` files and
  `DUCKDB_VECTORS_BACKEND=local` configs must be re-indexed/updated.
- __Default embedding model changed__ to `fastembed:minishlab/potion-multilingual-128M`
  (256-dim, static, multilingual) — smaller baked indexes and international
  coverage out of the box, at some relevance cost versus a contextual model.

### Added

- __Hugging Face embedding loader__ (`hf:<repo-id>`): downloads any HF ONNX
  model and runs it through the onnxruntime fastembed already vendors — no
  torch, no new runtime. Flagship example: `hf:Qwen/Qwen3-Embedding-0.6B`.
- __Multilingual embedding ladder__ via fastembed: 256 / 384 / 768 / 1024, plus
  English `bge` tiers, selectable by string.
- __Filesystem-aware chunker contract__: chunkers own the recursive folder walk
  (`chunk_folder` / `chunk_file` / `handles`) with begin/end lifecycle hooks at
  the indexing, folder, and file levels; a `FilesystemChunker` base keeps
  concrete chunkers small.
- __PDF ingestion__ (`pdf` chunker): per-page text via `pypdf`, a local RapidOCR
  fallback for scanned pages, and page-aware chunks carrying `metadata["page"]`
  and `{source}#p{N}:{pos}` ids. `pypdf`, `pypdfium2`, and `rapidocr-onnxruntime`
  ship in the base install.
- __Composite chunker__ (`composite`): indexes a mixed-type folder in one pass,
  dispatching each file to the first registered chunker that claims it; a newly
  registered chunker participates automatically.
- __Performance guide__: a measured retrieval benchmark (p50/p95 on a stated
  machine), a storage-then-compute engineering explanation, the brute-force vs
  HNSW decision, and an in-process vs network-vector-database comparison. Plus
  `scripts/benchmark_performance.py` and `scripts/benchmark_embedders.py`.
- __Real-document PDF fixtures and non-regression tests__ under `fixtures/pdf/`,
  with a synthetic scanned PDF that exercises the OCR path offline.
- __Use-case guide__: set up a local knowledge base in Kilo Code (VS Code) over
  stdio MCP.

### Changed

- __Documentation data-architecture summary__ aligned across the README, the
  website home, and the performance guide; the external blog post corrected to
  observed facts.
- __VitePress Makefile targets renamed__ to `<verb>-documentation`
  (`setup-documentation`, `serve-documentation`, `preview-documentation`) and
  cleanup consolidated into `clean`.

## v261019

Prior release. See the git history for details.
