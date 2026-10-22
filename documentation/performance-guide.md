# Performance guide

How fast is in-process search, and how much data fits in one container? This page answers both: first a measured retrieval benchmark, then the engineering model that explains the shape of the numbers and extrapolates past what was measured.

> __Two kinds of number live on this page.__ The [measured retrieval latency](#measured-retrieval-latency) section reports real p50/p95 from a timing run on a specific machine, produced by the harness that ships with the package (`scripts/benchmark_performance.py`). Everything after it — transport overhead, embedder comparison, and the data-volume ceiling — is an engineering __model__ derived from the architecture, with source files cited. Model figures set expectations and extrapolate; the measured section is the ground truth at the one scale we timed. Where the two disagree, the measurement wins, and the model text below has been corrected to match it.

## Measured retrieval latency

A real timing run, retrieval only — the index is built once (indexing cost is not measured) and then warm `query()` calls are timed.

__Setup:__ 1,000 chunks of ~300 words each, local embedder (`BAAI/bge-small-en-v1.5`, 384-dim, fastembed ONNX, fully offline), hybrid search (`top_k=5`), 5 warm-up queries discarded, 50 timed iterations over a 5-query bank. __Hardware: MacBook Pro M1 (2021).__ Database file: 9.5 MB. Reproduce with `scripts/benchmark_performance.py`.

| Slice | p50 | p95 | p99 |
| --- | --- | --- | --- |
| Query embedding | 4.4 ms | 5.0 ms | 5.1 ms |
| Full `query()` (embed + scan + fuse + fetch) | 41.4 ms | 42.5 ms | 42.6 ms |
| Scan + fuse + fetch (query − embed, p50) | ~37 ms | | |

Two things stand out, and both correct the model's first guess:

- __The cosine scan, not the embedding, dominates.__ Query embedding is small and steady (~4.4 ms, exactly as the model predicts below). The rest of `query()` — the cosine scan, the BM25 scan, Reciprocal Rank Fusion, and the per-hit fetch-by-id — is ~37 ms at only 1,000 chunks. Profiling that ~37 ms (next section) shows the brute-force cosine scan is by far the largest slice at ~32 ms (~80% of retrieval), with BM25 at ~6 ms and fusion plus fetch a rounding error. An early pure-cosine model got the shape right but under-counted the absolute cost of the cosine scan itself.
- __The tail is tight.__ p95 and p99 sit within ~1 ms of p50. In-process execution has no network hop to stall on, so latency is almost flat across iterations — the practical value of the embedded design.

The absolute numbers are specific to this machine and this corpus shape; treat them as an order-of-magnitude anchor (tens of milliseconds), not a universal constant. The model below explains why the slices behave as they do and how they scale.

### Where the ~41 ms goes

Profiled separately on the same machine (MacBook Pro M1 (2021), 1,000 chunks, local embedder, hybrid `query()` `top_k=5`, 50 timed iterations), the retrieval time breaks down as follows. These are __measured__ figures, not a model.

| Slice | Time | Share |
| --- | --- | --- |
| Cosine vector scan (`array_cosine_distance` over `FLOAT[384]`) | ~32 ms | ~80% |
| BM25 exact-token scan | ~6 ms | ~15% |
| Reciprocal Rank Fusion | ~0.03 ms | ~0% |
| Per-id fetch loop | ~2 ms | ~5% |

The headline: the brute-force cosine scan is the budget. BM25 is a small second, fusion is free, and the fetch loop is negligible. This is why corpus size moves the number — the cosine scan is the slice that grows linearly with row count.

## How a query spends its time

A single search decomposes into four slices:

```text
transport  +  embed  +  scan  +  fuse/fetch
```

Source: `src/duckdb_vectors/store.py`, `query()`. The method embeds the query string exactly once (`embedding = self.embedder.embed([text])[0]`), then runs two ranking scans over the same DuckDB file:

- a __brute-force cosine scan__ — `array_cosine_distance(embedding, $embedding)` over a `FLOAT[dim]` ARRAY column. There is __no HNSW index__ on the default path; the schema is HNSW-ready (a fixed-size ARRAY column) but the default query is a linear scan of every row.
- a __BM25 scan__ — the lexical signal over the full-text index.

The two rankings are fused with Reciprocal Rank Fusion, then the top-k chunks are fetched by id.

The key insight for a __fair comparison__: `embed`, `scan`, and `fuse/fetch` are identical across every transport — the same model runs on the same input and the same DuckDB file is scanned the same way, regardless of how the call arrived. So the __only__ slice that differs between in-process, stdio MCP, and HTTP MCP is `transport`. A fair comparison holds the retrieval cost fixed and compares just that one slice. Measurement (above) puts the fixed retrieval cost at ~41 ms for a 1k-chunk index, of which the brute-force cosine scan — not the embedding, and not BM25 or fusion — is the dominant part (~32 ms, ~80%).

## Transport overhead: in-process vs stdio vs HTTP MCP

Transport is the only slice a transport choice changes. Per call:

| Transport | What happens | Per-call overhead |
| --- | --- | --- |
| In-process (`VectorStore.query`) | a Python function call; no serialization, no IPC | ~0 ms (sub-microsecond) |
| stdio MCP (`duckdb-vectors mcp`) | JSON-RPC over a same-container pipe to a child process; no network stack | ~0.1-2 ms |
| HTTP MCP (remote) | JSON-RPC over HTTP — serialize, TCP/TLS, network hop, remote handling, response back | ~10-50 ms same-region; 50-150 ms+ cross-region or cold connection |

Source for the stdio path: `src/duckdb_vectors/mcp_server.py`. An MCP client spawns `duckdb-vectors mcp` as a subprocess in the __same container__ and talks to it over stdin/stdout. stdio is a same-container IPC mechanism, __not__ a network protocol — there is no socket, no TLS handshake, no hop. That is why stdio gives you the MCP tool contract at essentially in-process latency: you get the standard tool interface a client expects without paying the network cost that "MCP" usually implies.

## Would an HNSW index help?

The cosine scan is the budget, so the obvious question is whether an approximate-nearest-neighbour (ANN) index would pay off. We measured it: the same 1,000-chunk index, built with an HNSW index using the DuckDB `vss` extension (cosine metric, requiring the experimental-persistence flag), compared against the brute-force exact top-k as ground truth.

| Approach | Vector-ranking p50 | Recall@5 |
| --- | --- | --- |
| Brute-force exact scan | ~30.1 ms | 1.00 (ground truth) |
| HNSW index (vss extension) | ~29.6 ms | 0.96 mean, 0.80 worst-case |

The planner did use the index (`HNSW_INDEX_SCAN` confirmed in the query plan), so this is a real HNSW path, not a silent fall-back. The result: at 1,000 chunks HNSW is ~2% faster — within noise — while giving up exact recall (0.96 mean, down to 0.80 on the worst query) and requiring the experimental-persistence flag for a baked read-only file. At this scale the distance-comparison count is small enough that brute force is already fast, so the index buys essentially nothing in speed and costs recall.

__Conclusion:__ brute force is the right default at the package's target scale. HNSW only becomes a worthwhile lever well beyond the thousands-of-items niche, where the number of distance comparisons itself dominates the budget. The schema stays HNSW-ready (a fixed-size ARRAY column) so a user who outgrows the niche can opt in, but the default path pays neither the recall nor the persistence-flag cost.

## Versus a network vector database

How does the in-process path compare to reaching a hosted vector database over the network? Hold the retrieval work fixed and look at what the network adds.

- __In-process (this package):__ ~41 ms p50 at 1,000 chunks on the reference machine, with __no network hop__ — the index is baked read-only into the agent's container and queried by a local function call.
- __Hosted vector database:__ its own compute (embedding and/or ANN search on the server) __plus__ a network round-trip to reach it. The round-trip alone is ~10-50 ms same-region and 50-150 ms+ cross-region or on a cold connection (modelled, see the transport table above), with a long tail that in-process execution does not have.

The honest boundary: at this 1k scale the network hop roughly __doubles__ end-to-end latency (~30-50 ms added on top of comparable retrieval work), and none of the added time does any more searching. A remote store only starts to pay for itself once the corpus outgrows what a brute-force scan serves well — well past the thousands-to-low-tens-of-thousands niche this package targets. Below that line, the file baked into the container is both faster and simpler; above it, the served store's indexing and horizontal scale earn their round-trip. The network figures are a __model__; the ~41 ms in-process figure is measured.

## Embedding latency dominates

Because `embed` is the same across transports, its size decides how much the transport slice matters. The two built-in embedders sit at very different points.

- __Local__ (`src/duckdb_vectors/embedders/local.py`, default `BAAI/bge-small-en-v1.5`, 384-dim, fastembed ONNX, no network): roughly __5-20 ms__ per short query on a warm CPU. No round-trip — the model runs in-process.
- __Bedrock__ (`src/duckdb_vectors/embedders/bedrock.py`, Titan `amazon.titan-embed-text-v2:0`, 1024-dim): one `invoke_model` HTTP round-trip per query, roughly __30-100 ms__ same-region. This is a network call regardless of which transport delivered the search request.

Worked end-to-end latency, per embedder and transport. The `scan + fuse/fetch` term is taken from the measured section above (~37 ms at 1k chunks on the reference machine, of which ~32 ms is the cosine scan), not from the earlier pure-cosine estimate; it moves with corpus size and hardware, so treat these as that machine's figures:

| Local embedder (384-dim) | In-process | stdio MCP | HTTP MCP |
| --- | --- | --- | --- |
| End-to-end | ~41 ms | ~42-43 ms | ~75-90 ms |

With the local embedder, embedding is the small slice (~4 ms) and the cosine scan is the large one (~32 ms of the ~37 ms scan+fetch); transport is what a transport choice changes on top of that fixed retrieval cost. Moving from in-process/stdio to a remote HTTP MCP adds ~30-50 ms of pure network overhead — on this corpus that roughly __doubles__ end-to-end latency, and none of the added time does any more searching.

| Bedrock embedder (1024-dim) | In-process | stdio MCP | HTTP MCP |
| --- | --- | --- | --- |
| End-to-end | ~67-137 ms | ~68-138 ms | ~97-187 ms |

With Bedrock the embedding network round-trip (~30-100 ms, modelled — not measured here) stacks on top of the ~37 ms scan, so a single search already spans two cost centres before transport. In-process still wins and stdio is within a hair of it, but the relative value of avoiding the HTTP transport shrinks because the embed call is itself a network cost you cannot avoid.

The win is not only latency. In-process and stdio also buy:

- __No network tail-latency variance__ — p99 stays near p50 because there is no hop to occasionally stall. A remote HTTP call adds a long tail that the model above does not even show.
- __No separate service__ to run, secure, scale, or pay for — the index travels inside the agent image.
- __Cheap cold start__ — bringing the store up is a read-only file open. The one real warm-up is loading the local embedding model once, and that cost is paid regardless of transport.

## How much data fits in a container

The ceiling is set by retrieval time, not by disk. The DuckDB file is small. Retrieval has a small __fixed floor__ — the measured ~8 ms of embedding, BM25, fusion, and per-id fetch that is present even at 1,000 chunks — plus a __linear term__ that dominates: the brute-force cosine scan, measured at ~32 ms for 1,000 chunks and growing with row count. Below, the "retrieval latency" column is that floor plus the growing scan; it is anchored on the measured 1k point and extrapolated upward (model, not measured).

Per-chunk footprint, from the defaults:

- __Text__ — the markdown chunker's default `target_words = 300` (`src/duckdb_vectors/chunkers/markdown.py`, `DEFAULT_TARGET_WORDS`) is roughly 1.5-2 KB of text per chunk.
- __Vector__ — the default local embedding is `FLOAT[384]` = 384 x 4 = 1,536 bytes.
- __Rest__ — etag, metadata JSON, title, and the BM25 full-text index.

Round figure: __~3-5 KB stored per chunk__.

| Chunks | Approx DB size | Approx raw text | Retrieval latency (local, 384-dim) | Verdict |
| --- | --- | --- | --- | --- |
| 1,000 | ~3-5 MB | ~1.5-2 MB | ~40 ms (measured) | the reference point |
| 10,000 | ~30-50 MB | ~15-20 MB | ~45-60 ms | comfortable sweet spot |
| 50,000 | ~150-250 MB | ~75-100 MB | ~70-120 ms | fine; scan now visible |
| 100,000 | ~300-500 MB | ~150-200 MB | ~120-200 ms | upper edge of snappy |
| 500,000+ | ~1.5-2.5 GB+ | ~1 GB+ | several hundred ms+ | brute force starts to hurt |

The 1k row is measured; the rest adds the linear cosine-scan growth on top of the fixed floor and is a model extrapolation, not a measurement. Run the harness at your own scale to replace any row with a real figure.

Practical guidance:

- The __design target__ is a few thousand to a few tens of thousands of chunks — the band where retrieval stays in tens of milliseconds and the DB file is negligible next to a typical container image.
- The __comfortable ceiling__ for the default (no HNSW) is around __100k chunks__: retrieval is still interactive (low hundreds of ms) and the file is still a rounding error against the image. If you need the single-digit-to-low-tens-of-ms band, stay nearer the low-thousands scale that was measured.
- __Beyond ~100k-500k__, it is the linear scan — not storage — that degrades. Options, in order of effort:
  1. A __smaller embedding dimension__ scans faster (384-dim is roughly 2x faster to scan than 768-dim).
  2. __Scope the query__ with a metadata `WHERE` in the injectable ranking SQL to cut the scanned set.
  3. __Build the HNSW index__ the schema already supports, accepting the experimental-persistence flag and its read-only tradeoffs.

Honest caveat: because the index is baked read-only into the image, "how much fits" is a __build-time__ decision, not something that scales at runtime. If the corpus outgrows the comfortable scan range, the answer is to narrow per-query scope or to graduate to an indexed or served store. The package deliberately targets the embedded, thousands-to-low-tens-of-thousands niche — exactly the scale that keeps the "tens of milliseconds" claim on the [home page](index) honest.
