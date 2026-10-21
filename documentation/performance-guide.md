# Performance guide

How fast is in-process search, and how much data fits in one container? This page answers both with an engineering model you can reason about, so you can decide whether `duckdb-vectors` fits before you measure.

> __These figures are an engineering model, not a measured benchmark.__ They are derived from the architecture — the source files are cited at every claim — and are meant to set expectations and ground design choices. They are __not__ p50/p95 numbers from a timing harness. A measured harness is tracked separately (see the optional item on issue #25) and will replace these estimates with real percentiles when it lands. Until then, read every number below as "the model predicts," not "we measured."

## How a query spends its time

A single search decomposes into four slices:

```text
transport  +  embed  +  scan  +  fuse/fetch
```

Source: `src/duckdb_vectors/store.py`, `query()`. The method embeds the query string exactly once (`embedding = self.embedder.embed([text])[0]`), then runs two ranking scans over the same DuckDB file:

- a __brute-force cosine scan__ — `array_cosine_distance(embedding, $embedding)` over a `FLOAT[dim]` ARRAY column. There is __no HNSW index__ on the default path; the schema is HNSW-ready (a fixed-size ARRAY column) but the default query is a linear scan of every row.
- a __BM25 scan__ — the lexical signal over the full-text index.

The two rankings are fused with Reciprocal Rank Fusion, then the top-k chunks are fetched by id.

The key insight for a __fair comparison__: `embed` is identical across every transport — the same model runs on the same input regardless of how the call arrived — and `scan` and `fuse/fetch` happen inside the same DuckDB file either way. So the __only__ slice that differs between in-process, stdio MCP, and HTTP MCP is `transport`. Compare transports honestly and you are comparing just that one slice against a fixed `embed + scan` cost that dominates it.

## Transport overhead: in-process vs stdio vs HTTP MCP

Transport is the only slice a transport choice changes. Per call:

| Transport | What happens | Per-call overhead |
| --- | --- | --- |
| In-process (`VectorStore.query`) | a Python function call; no serialization, no IPC | ~0 ms (sub-microsecond) |
| stdio MCP (`duckdb-vectors mcp`) | JSON-RPC over a same-container pipe to a child process; no network stack | ~0.1-2 ms |
| HTTP MCP (remote) | JSON-RPC over HTTP — serialize, TCP/TLS, network hop, remote handling, response back | ~10-50 ms same-region; 50-150 ms+ cross-region or cold connection |

Source for the stdio path: `src/duckdb_vectors/mcp_server.py`. An MCP client spawns `duckdb-vectors mcp` as a subprocess in the __same container__ and talks to it over stdin/stdout. stdio is a same-container IPC mechanism, __not__ a network protocol — there is no socket, no TLS handshake, no hop. That is why stdio gives you the MCP tool contract at essentially in-process latency: you get the standard tool interface a client expects without paying the network cost that "MCP" usually implies.

## Embedding latency dominates

Because `embed` is the same across transports, its size decides how much the transport slice matters. The two built-in embedders sit at very different points.

- __Local__ (`src/duckdb_vectors/embedders/local.py`, default `BAAI/bge-small-en-v1.5`, 384-dim, fastembed ONNX, no network): roughly __5-20 ms__ per short query on a warm CPU. No round-trip — the model runs in-process.
- __Bedrock__ (`src/duckdb_vectors/embedders/bedrock.py`, Titan `amazon.titan-embed-text-v2:0`, 1024-dim): one `invoke_model` HTTP round-trip per query, roughly __30-100 ms__ same-region. This is a network call regardless of which transport delivered the search request.

Worked end-to-end latency (transport + embed + ~1-5 ms scan), per embedder and transport:

| Local embedder (384-dim) | In-process | stdio MCP | HTTP MCP |
| --- | --- | --- | --- |
| End-to-end | ~6-25 ms | ~7-26 ms | ~36-55 ms |

With the local embedder the in-process/stdio win is __largest__: the embedding work is small, so transport was often bigger than the real work. HTTP MCP runs roughly __2-8x slower end-to-end__, and that extra time is pure overhead — no additional search happened.

| Bedrock embedder (1024-dim) | In-process | stdio MCP | HTTP MCP |
| --- | --- | --- | --- |
| End-to-end | ~31-105 ms | ~32-106 ms | ~61-135 ms |

With Bedrock the embedding network call dwarfs transport, so the transport slice is a smaller share (~20-30% of the total) — but in-process still wins, and stdio is within a hair of it.

The win is not only latency. In-process and stdio also buy:

- __No network tail-latency variance__ — p99 stays near p50 because there is no hop to occasionally stall. A remote HTTP call adds a long tail that the model above does not even show.
- __No separate service__ to run, secure, scale, or pay for — the index travels inside the agent image.
- __Cheap cold start__ — bringing the store up is a read-only file open. The one real warm-up is loading the local embedding model once, and that cost is paid regardless of transport.

## How much data fits in a container

The ceiling is set by the __brute-force scan__ (linear in chunk count), not by disk. The DuckDB file is small; the question is how long a linear cosine scan over every row takes.

Per-chunk footprint, from the defaults:

- __Text__ — the markdown chunker's default `target_words = 300` (`src/duckdb_vectors/chunkers/markdown.py`, `DEFAULT_TARGET_WORDS`) is roughly 1.5-2 KB of text per chunk.
- __Vector__ — the default local embedding is `FLOAT[384]` = 384 x 4 = 1,536 bytes.
- __Rest__ — etag, metadata JSON, title, and the BM25 full-text index.

Round figure: __~3-5 KB stored per chunk__.

| Chunks | Approx DB size | Approx raw text | Scan latency (local, 384-dim) | Verdict |
| --- | --- | --- | --- | --- |
| 1,000 | ~3-5 MB | ~1.5-2 MB | sub-millisecond | trivial |
| 10,000 | ~30-50 MB | ~15-20 MB | ~1-5 ms | comfortable sweet spot |
| 50,000 | ~150-250 MB | ~75-100 MB | ~5-20 ms | fine; scan now visible |
| 100,000 | ~300-500 MB | ~150-200 MB | ~15-40 ms | upper edge of snappy |
| 500,000+ | ~1.5-2.5 GB+ | ~1 GB+ | ~100 ms+ per query | brute force starts to hurt |

Practical guidance:

- The __design target__ is a few thousand to a few tens of thousands of chunks — the band where the scan is a rounding error and the DB file is negligible next to a typical container image.
- The __comfortable ceiling__ for the default (no HNSW) is around __100k chunks__: the scan stays within the "tens of milliseconds" promise and the file is still a rounding error against the image.
- __Beyond ~100k-500k__, it is the linear scan — not storage — that degrades. Options, in order of effort:
  1. A __smaller embedding dimension__ scans faster (384-dim is roughly 2x faster to scan than 768-dim).
  2. __Scope the query__ with a metadata `WHERE` in the injectable ranking SQL to cut the scanned set.
  3. __Build the HNSW index__ the schema already supports, accepting the experimental-persistence flag and its read-only tradeoffs.

Honest caveat: because the index is baked read-only into the image, "how much fits" is a __build-time__ decision, not something that scales at runtime. If the corpus outgrows the comfortable scan range, the answer is to narrow per-query scope or to graduate to an indexed or served store. The package deliberately targets the embedded, thousands-to-low-tens-of-thousands niche — exactly the scale that keeps the "tens of milliseconds" claim on the [home page](index) honest.
