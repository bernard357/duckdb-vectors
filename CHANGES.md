# Changelog

All notable changes to `duckdb-vectors` are recorded here. Versions are
tag-driven (`vYYMMDD`); pushing a tag publishes the release to PyPI.

## Unreleased

### Breaking

- __Read-only store mode removed.__ The `read_only` field on `Settings`, the
  `DUCKDB_VECTORS_READ_ONLY` environment variable, the `read_only` argument on
  `VectorStore`, and the `ReadOnlyError` class are all gone. A `VectorStore`
  now always opens writable and ensures its schema — one store mode for both
  reading and writing. The flag had no defined use case and blocked
  write-on-startup patterns (indexing and incremental re-indexing); callers
  passing `read_only=...` or catching `ReadOnlyError` must drop it.

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
