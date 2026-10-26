# User guide

Is `duckdb-vectors` adapted to your use case? And how to use it? Let see how you can leverage the package for your agent.

If you are looking for an internal view of `duckdb-vectors`, then jump to the [Technical design](duckdb-vectors-primitive.md) page. And if you want to contribute, go to the [Developer workbook](developer-workbook.md).

## Is `duckdb-vectors` a good fit for your use case?

In-process semantic search fits when the corpus is in the thousands of items, mostly static, and travels with a single agent, and when a local function call beating a network round trip matters more than elastic write throughput. It does __not__ replace a managed vector database for large multi-tenant corpora, high-churn write workloads, or sustained high-concurrency serving. Naming the boundary is part of the pitch: the smaller scope is exactly what buys the simplicity.

The mismatch is easiest to see side by side:

| Facet | Separate vector database | `duckdb-vectors` |
| --- | --- | --- |
| Corpus size | millions of vectors, sharded | a few thousand items, in one file |
| Write pattern | high-churn, live updates | read-mostly, re-indexed on build |
| Deployment | a separate service to run | a file baked into the agent image |
| Query path | MCP or network round trip | local call, in-process execution |
| Concurrency | scalable back-end service | scale-out by design |
| You operate | deploy, secure, monitor, pay | `pip install` and `Dockerfile` |

Pick the left column when you genuinely have that scale. For the right column — the in-process semantic search — the left column is machinery you do not need.

For where "a few thousand items" actually tops out — the chunk-count ceiling, the resulting database size, and how scan latency grows — see the data-volume section of the [performance guide](performance-guide).

## How to install `duckdb-vectors`?

This package is exposed publicly from [PyPI](https://pypi.org/project/duckdb-vectors/).

```shell
$ pip install duckdb-vectors             # install once, batteries included
```

As usual, you can also use `uv` for the installation, and you can also add `duckdb-vectors` to some `requirements.txt`.

The install brings the `duckdb-vectors` command. Run it with no arguments, or with `--help`, to list the verbs and their options:

```shell
$ duckdb-vectors --help          # list the index, report, search, and mcp verbs
$ duckdb-vectors index --help    # options for a specific verb
$ duckdb-vectors --version       # print the tag-derived package version
```

## How to gather content?

Collect the documents you want searchable in one place. Markdown is first-class input because the markdown chunker understands headings. PDF is first-class too, through the `pdf` chunker — it reads per-page text and falls back to local OCR for scanned pages (see [PDF ingestion](#pdf-ingestion) below). Both are claimed automatically by the composite default via their MIME type; convert other formats (HTML, docx) to markdown first.

```text
documentation/
  getting-started.md
  architecture.md
  faq/
    billing.md
    limits.md
```

A folder on the local filesystem is the simplest source. The same folder can come from an S3 sync, a git checkout, or a wiki export — `duckdb-vectors` needs files on disk at index time.

Enrich each markdown page with meta-attributes that you want to use at search time. A YAML frontmatter block at the top of a page is parsed and merged into every chunk of that page:

```markdown
---
kind: faq
product: billing
audience: customer
---

# Refund window

Refunds are processed within 14 days of the request.
```

Each key lands in `chunk.metadata`, is returned with every hit, and can be referenced from the ranking SQL (see [How to search](#how-to-search-for-matching-content)). The chunker's own `section_path` is always preserved, so frontmatter keys never overwrite it.

## How to index content?

Indexing chunks each document, embeds the chunks, and writes them to a DuckDB file. The folder indexer embeds only what changed since the last run.

You can use either the CLI or the python interface.

Two choices matter for indexing: the __chunker__ that splits documents and the __embedding backend__ that vectorises the chunks. Both default sensibly — the composite chunker dispatches each file to the registered chunker claiming its MIME type, and `fastembed:minishlab/potion-multilingual-128M` is the default embedder. The embedding backend is selectable by string; see [Chunkers](#chunkers) and [Embedding backends](#embedding-backends) below. The embedding backend identity is folded into each chunk's ETag, so switching backends re-embeds everything correctly.

Here is an example of indexing with python:

```python
from duckdb_vectors import VectorStore, index_folder

store = VectorStore(db_path="vectors.duckdb", backend="fastembed:minishlab/potion-multilingual-128M")
report = index_folder(store, "documentation", recursive=True)
print(report)  # files, chunks, embedded, reused, pruned
store.close()
```

`VectorStore` accepts these arguments:

- `db_path` — the DuckDB file (default `vectors.duckdb`).
- `backend` — the embedding backend spec, e.g. `fastembed:<model>` or `bedrock:titan` (default `fastembed:minishlab/potion-multilingual-128M`).
- `chunker` — the chunker spec, e.g. `markdown` or `pdf` (default `markdown`).
- `read_only` — open the file without writing; the serving default (default `false`).
- `embedder` — pass a ready `Embedder` instance to bypass backend resolution.
- `settings` — pass a fully-built `Settings` object instead of the individual arguments.

Each setting resolves with the precedence explicit argument > `DUCKDB_VECTORS_*` environment variable > default, so the same configuration works from code or from the environment — handy in a container:

| Setting | Argument | Environment variable | Default |
| --- | --- | --- | --- |
| Database path | `db_path` | `DUCKDB_VECTORS_DB_PATH` | `vectors.duckdb` |
| Embedding backend | `backend` | `DUCKDB_VECTORS_BACKEND` | `fastembed:minishlab/potion-multilingual-128M` |
| Read-only | `read_only` | `DUCKDB_VECTORS_READ_ONLY` | `false` |

### Embedding backends

A backend is resolved from a string — a `name:variant` pair (`fastembed:<model>`, `hf:<repo-id>`, `bedrock:titan`) or a bare name (`hashing`). The default is `fastembed:minishlab/potion-multilingual-128M`, a static multilingual model that keeps vectors and the DuckDB file as small as possible. The registry is contributor-extensible, so you can register an open or closed, local or cloud backend without touching the core:

```python
from duckdb_vectors import register_embedder

register_embedder("mybackend", lambda variant: MyEmbedder(variant))
```

The `fastembed:` backend runs a bundled ONNX model with no torch dependency. Pick the dimension tier that fits your relevance/size budget:

| Backend | Spec | Dimensions | Use for |
| --- | --- | --- | --- |
| fastembed potion (static, multilingual) | `fastembed:minishlab/potion-multilingual-128M` | 256 | default; smallest DB, international docs |
| fastembed MiniLM (multilingual) | `fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384 | balanced multilingual relevance |
| fastembed mpnet (multilingual) | `fastembed:sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | 768 | higher multilingual relevance |
| fastembed e5-large (multilingual) | `fastembed:intfloat/multilingual-e5-large` | 1024 | strongest multilingual relevance |
| fastembed bge-small (English) | `fastembed:BAAI/bge-small-en-v1.5` | 384 | English-only, compact |
| fastembed bge-large (English) | `fastembed:BAAI/bge-large-en-v1.5` | 1024 | English-only, strongest |
| Hugging Face ONNX (any repo) | `hf:<repo-id>`, e.g. `hf:Qwen/Qwen3-Embedding-0.6B` | probed | any HF repo shipping ONNX weights |
| Bedrock Titan | `bedrock:titan` | 1024 | cloud embedding |
| Hashing (deterministic, no deps) | `hashing` | 384 | tests and CI only, not relevance |

The `hf:<repo-id>` backend is core (no extra install): it downloads a repository's ONNX weights and `tokenizer.json` via `huggingface_hub`, runs them through the ONNX runtime fastembed already vendors, mean-pools by default, and uses last-token pooling for Qwen3-style repos. Use it to load a model fastembed does not bundle. It now loads the large majority of ONNX embedding exports on the Hub, not just single-file sentence-transformer encoders. A repo must publish ONNX weights (for example `onnx/model.onnx`) for this backend to load it.

The loader handles three ONNX shapes:

- __Simple encoders__ (bge/e5/sentence-transformers): `input_ids` / `attention_mask` (/ `token_type_ids`) feeding a `last_hidden_state` output.
- __External-data ONNX__: larger exports whose weights live in a sibling `model.onnx_data`. The loader snapshot-downloads the ONNX folder and co-locates the real files in one directory so the ONNX runtime finds the data file beside the graph.
- __Decoder-style embedding graphs__ (Qwen3-Embedding and relatives): graphs that declare `position_ids` and per-layer `past_key_values.*` cache inputs. The loader synthesizes `position_ids` from the attention mask, feeds zero-length cache tensors (shapes read from the graph), and selects the hidden-state output by name (never the vocabulary `logits`).

A worked Qwen example — the repo id carries `qwen`, so the backend switches to last-token pooling automatically (no `pooling` argument needed):

```python
from duckdb_vectors import VectorStore, index_folder

store = VectorStore(
    db_path="vectors.duckdb",
    backend="hf:onnx-community/Qwen3-Embedding-0.6B-ONNX",
)
index_folder(store, "documentation", recursive=True)
```

`onnx-community/Qwen3-Embedding-0.6B-ONNX` is an external-data, decoder-style export (external `model.onnx_data`, `position_ids` plus 28 layers of KV-cache inputs) that emits a 1024-dimensional `last_hidden_state`; it exercises every part of the generalized loader. On first use the backend downloads the ONNX weights and `tokenizer.json` into the `huggingface_hub` cache (a few hundred megabytes); subsequent runs reuse the cache and need no network. A repo must publish ONNX weights at a path the loader probes (`onnx/model.onnx`, `model.onnx`, `onnx/model_quantized.onnx`, or `model_quantized.onnx`) in a form the vendored ONNX runtime can run; a repo that ships only PyTorch weights will not load.

### Chunkers

Indexing uses the composite chunker as its default dispatch path. Each chunker claims one or more MIME types: `handles` resolves a path's MIME type via the stdlib `mimetypes.guess_type` and tests membership in the chunker's declared set, so `.md` and `.MD` (both `text/markdown`) route to the same chunker. The registry is contributor-extensible, so you can register your own splitter for other formats or strategies without touching the core:

```python
from duckdb_vectors import register_chunker

register_chunker("mychunker", lambda variant: MyChunker(variant))
```

A chunker owns the folder walk, not just text splitting. The contract has three parts:

- __Traversal__ — `chunk_folder(folder, recursive=True)` walks the tree and yields chunks for every file the chunker claims, setting each chunk's `source` to its path relative to the folder root.
- __Per-file handling__ — a chunker declares a `mimetypes` frozenset of the MIME types it claims (the markdown chunker claims `text/markdown`), and the base `handles(path)` matches a path's MIME type against it; a subclass may still override `handles` when MIME detection is ambiguous. `chunk_file(path)` reads one file and yields its chunks. `chunk(text, source)` stays available for direct text input.
- __Lifecycle hooks__ — three begin/end pairs with no-op defaults fire in a strict nesting order: `begin_indexing` / `end_indexing` wrap a whole run, `begin_folder` / `end_folder` wrap each directory, and `begin_file` / `end_file` wrap each handled file. `end_indexing` always runs, even if indexing raises.

Custom chunkers subclass `FilesystemChunker` and implement only the `mimetypes` declaration and `chunk_file`; the base provides MIME-based matching, the recursive walk, the relative-`source` assignment, and the no-op hooks. The `pdf` chunker is the second consumer: it claims `application/pdf`, loads a local OCR engine once in `begin_indexing`, and emits one chunk per page from `chunk_file` (see [PDF ingestion](#pdf-ingestion)).

| Chunker | MIME type(s) | Use for |
| --- | --- | --- |
| MarkdownChunker | `text/markdown` | heading-scoped sections with word-window splitting |
| PdfChunker | `application/pdf` | per-page text with local OCR for scanned pages |
| CompositeChunker | dispatches by MIME type | default; dispatches a mixed folder to the matching chunker per file in one pass |

### Indexing a mixed folder in one pass

A real folder often mixes formats — some `.md`, some `.pdf`. The composite chunker is the default indexing path, so `index_folder` handles a mixed folder in a single pass with no selector: it dispatches each file to the first registered chunker that claims its MIME type, so markdown files go to the markdown chunker and PDFs go to the PDF chunker, with ids byte-identical to what each chunker would produce on its own.

```python
from duckdb_vectors import VectorStore, index_folder

store = VectorStore(db_path="vectors.duckdb")
report = index_folder(store, "documents", recursive=True)
```

The composite builds its members from every other registered chunker, so coverage grows with the registry. There are two ways to extend format coverage: open a __PR against an existing chunker__ (for example to teach the PDF chunker a new OCR path), or __register a new chunker__ with `register_chunker` for a format no chunker claims yet. A newly registered chunker is picked up by the composite automatically — no extra wiring.

### PDF ingestion

The `pdf` chunker reads a PDF one page at a time. The composite default already dispatches `.pdf` files to it, but you can force PDF-only indexing with the code-level `chunker=` override:

```python
from duckdb_vectors import PdfChunker, VectorStore, index_folder

store = VectorStore(db_path="vectors.duckdb")
report = index_folder(store, "pdfs", chunker=PdfChunker(), recursive=True)
```

Each chunk is page-scoped: `metadata["page"]` carries the 1-based page number and the chunk id is `{source}#p{N}:{pos}`, so re-indexing upserts in place even as pages shift. A page's text is split into the same overlapping word windows the markdown chunker uses, and one chunk is emitted per window.

A born-digital page carries an embedded text layer that is extracted directly. A scanned page has no usable text layer, so when a page's embedded text is shorter than the __min-chars threshold__ (default 16, tunable on the chunker) the page is rasterised and read with a local OCR engine. OCR runs __locally__ through `onnxruntime` — the same runtime `fastembed` already uses — so there is no cloud call on the indexing path. RapidOCR may fetch its ONNX models once on first real use; bake them into your build image to keep indexing self-contained.

To use a different OCR engine, register your own chunker. For example, a Tesseract-backed variant through `pytesseract` is a drop-in alternative you can register yourself:

```python
from duckdb_vectors import register_chunker

register_chunker("pdf-tesseract", lambda variant: MyTesseractPdfChunker())
```

See the README's [Embedding backends](https://github.com/bernard357/duckdb-vectors#embedding-backends) and [Chunking backends](https://github.com/bernard357/duckdb-vectors#chunking-backends) sections for the same tables at a glance.

Or from the shell:

```shell
$ duckdb-vectors index ./documentation --recursive --backend fastembed:minishlab/potion-multilingual-128M
```

The `index` verb takes `--db` (file path), `--backend` (embedding spec), `--recursive` (descend into sub-folders), and `--json` (emit the report as JSON instead of a human-readable line).

Check what landed in the database with the `report` verb — it prints the file path, backend, embedding dimensions, chunk count, and the indexed sources:

```shell
$ duckdb-vectors report                  # summary of database content
$ duckdb-vectors report --json           # same, as JSON
```

## How to search for matching content?

Query with a natural-language string. Search is hybrid by default: semantic
similarity plus exact-token BM25, fused with Reciprocal Rank Fusion.

```python
store = VectorStore(db_path="vectors.duckdb", backend="fastembed:minishlab/potion-multilingual-128M", read_only=True)
for hit in store.query("how do retries work?", top_k=5):
    print(f"{hit.score:.4f}  {hit.chunk.source}  {hit.chunk.title}")
```

Each ranking signal — vector and BM25 — runs a DuckDB `SELECT` that returns `chunk_id` best-first; the store collects the ids and fuses them with RRF. Restrict scope or decay by age by injecting your own `SELECT` — the ranking SQL owns its own `WHERE` and `ORDER BY`:

```python
scoped = (
    "SELECT chunk_id FROM {table} "
    "WHERE json_extract_string(metadata, '$.kind') = 'faq' "
    "ORDER BY {distance} LIMIT {top_k}"
)
hits = store.query("refund window", vector_sql=scoped, text_sql=scoped)
```

An injected template uses these substituted tokens: `{table}`, `{distance}` (vector signal only), `{bm25}` (text signal only), and `{top_k}`. The search text is available as the bound parameter `$q`, and these columns are in scope: `metadata`, `source`, `title`, `stamped_at`, `text`.

Overrides can be set once — shared across queries — via `Settings` or the `DUCKDB_VECTORS_VECTOR_SQL` / `DUCKDB_VECTORS_TEXT_SQL` environment variables, or passed per call as above. Precedence is per-call > settings > default; a custom `SELECT` owns its own `WHERE` and supersedes the convenience `filter` dict for that signal. See the README's [Metadata-aware search](https://github.com/bernard357/duckdb-vectors#metadata-aware-search-injectable-ranking-sql) section for more worked examples.

For a quick check from the shell, the `search` verb runs the same hybrid query and prints the ranked hits:

```shell
$ duckdb-vectors search "how do retries work?" --top-k 5
$ duckdb-vectors search "how do retries work?" --json      # structured output
```

The `mcp` verb serves the same retrieval as a Model Context Protocol server over stdio, so any MCP client can spawn `duckdb-vectors mcp` and call its read-only `search` tool. See the [Docker Agent guide](docker-agent-guide.md) for a complete integration.

```shell
$ duckdb-vectors mcp                     # serve retrieval over stdio
```

## How to integrate into a harness?

Inside an agent, retrieval becomes context. The pattern is: open the store at startup, then call `query` per user turn and feed the chunks to the model. This is framework-agnostic: the same store drops into a container, AWS Strands, Pydantic AI, etc. The examples below are basic snippets to be adapted to your own use case.

```python
from duckdb_vectors import VectorStore

store = VectorStore(db_path="vectors.duckdb", backend="fastembed:minishlab/potion-multilingual-128M", read_only=True)

def retrieve_context(question: str, k: int = 5) -> str:
    hits = store.query(question, top_k=k)
    return "\n\n".join(f"[{h.chunk.source}] {h.chunk.text}" for h in hits)

# Hand retrieve_context(question) to your LLM call as grounding, then let the
# model answer. duckdb-vectors stops at retrieval; the harness owns generation.
```

The frontmatter you attached at index time rides back on every hit as `hit.chunk.metadata`, so the harness can label citations, filter by attribute, or route on it:

```python
def retrieve_context(question: str, k: int = 5) -> str:
    blocks = []
    for h in store.query(question, top_k=k):
        kind = h.chunk.metadata.get("kind", "doc")
        blocks.append(f"[{kind}: {h.chunk.source}] {h.chunk.text}")
    return "\n\n".join(blocks)
```

See the [containerized agent guide](containerized-agent-guide.md) for building a self-contained agent image.

## How to build a container image?

Freeze the index into an image so the container is self-contained. Build the
index in a builder stage and copy only the frozen file into the runtime stage, then add the code of your agent:

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install duckdb-vectors
COPY documentation/ ./documentation/
RUN duckdb-vectors index ./documentation --recursive --backend fastembed:minishlab/potion-multilingual-128M --db /build/vectors.duckdb

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN pip install duckdb-vectors
COPY --from=builder /build/vectors.duckdb /app/vectors.duckdb
ENV DUCKDB_VECTORS_DB_PATH=/app/vectors.duckdb
ENV DUCKDB_VECTORS_READ_ONLY=true

COPY agent.py /app/agent.py
ENTRYPOINT ["python", "/app/agent.py", "--db", "/app/vectors.duckdb"]
CMD ["what is duckdb-vectors?"]
```

Once the `Dockerfile` has been built, it contains both the database and the agent to query it in-process:

```shell
$ docker build -f examples/containerized-agent/Dockerfile -t duckdb-vectors-agent .
$ docker run --rm duckdb-vectors-agent "how is a search answered?"
```

The frozen index is inside the image, so the container needs no volume and no network to answer.

## How to deploy?

Because the index is a file baked into the image, deployment is just "run the container" — there is no data tier to provision alongside it. The same artifact runs across targets:

- __Local / in-process__ — import `VectorStore` directly in a script or notebook, or `docker run` the image. No network, no service.

- __Containers (e.g., Kubernetes, ECS)__ — ship the image to any orchestrator. The container is stateless and read-only at serving time, so it scales horizontally by running more replicas; each replica reads its own baked index.

- __AWS Lambda__ — package the agentic code as a container image and deploy it as a Lambda function. The frozen index travels inside the image, so cold starts pay no data-loading or network cost. See the [AWS Lambda guide](aws-lambda-guide.md).

- __Amazon Bedrock AgentCore__ — run the container as an AgentCore runtime, letting AgentCore own the agent loop while `duckdb-vectors` serves retrieval in-process. See the [Bedrock AgentCore guide](aws-bedrock-agentcore-guide.md)
