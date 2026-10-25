# Changelog

All notable changes to `duckdb-vectors` are recorded here. Versions are
tag-driven (`vYYMMDD`); pushing a tag publishes the release to PyPI.

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
