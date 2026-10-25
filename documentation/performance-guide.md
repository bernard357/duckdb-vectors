# Performance guide

This page is an engineering explanation of how `duckdb-vectors` stores and retrieves content, and the performance you can expect from it. It starts with storage, then covers the compute cost of a query, then the design decisions that shape those numbers. Compute figures are measured on a MacBook Pro M1 (2021) over an index of 1,000 chunks; transport and remote-database figures are engineering estimates and are labelled as such.

## Storage

One DuckDB file is one index. It is built once and baked read-only into the agent's container image, so there is no data tier to provision and no network path to the data — a query is a local read of a local file.

The per-chunk footprint follows from the defaults. A default chunk is ~300 words (the markdown chunker's `DEFAULT_TARGET_WORDS` is 300), which is roughly 1.5-2 KB of text. The default embedding (`fastembed:minishlab/potion-multilingual-128M`) is a `FLOAT[256]` vector, exactly 256 x 4 = 1,024 bytes — the smallest tier on offer. A higher-dimension backend grows this term linearly: a 384-dim model is 1,536 bytes per vector, a 1,024-dim model 4,096 bytes. On top of that each chunk carries an etag, a metadata JSON, a title, and its entry in the BM25 full-text index. The round figure is __~3-5 KB stored per chunk__ at the default dimension. The measured anchor (at 384 dims, the previous default): an index of 1,000 chunks is __9.5 MB on disk__; the 256-dim default trims the vector share further.

Storage scales linearly with chunk count, and the file stays small relative to anything it ships inside:

| Chunks | Approx DB size |
| --- | --- |
| 1,000 | ~5-10 MB (measured anchor: 9.5 MB) |
| 10,000 | ~30-50 MB |
| 100,000 | ~300-500 MB |

Even at 100,000 chunks the file is a few hundred megabytes — negligible next to a typical container image. Storage is never the constraint. It scales linearly and cheaply, and because the file travels inside the agent image there is nothing to provision, secure, or pay for as a separate data tier.

## Compute

Retrieval is fast and flat. On the reference machine, a full hybrid `query()` with `top_k=5` over the 1,000-chunk index runs at __p50 41 ms, p95 43 ms__ — the tail sits within ~1 ms of the median, because in-process execution has no network hop to stall on. Embedding the query string alone is __4.4 ms__.

The ~41 ms breaks down as follows, measured on the same machine:

| Slice | Time | Share |
| --- | --- | --- |
| Cosine vector scan | ~32 ms | ~80% |
| BM25 exact-token scan | ~6 ms | ~15% |
| Reciprocal Rank Fusion | ~0.03 ms | ~0% |
| Per-id fetch loop | ~2 ms | ~5% |

The brute-force cosine scan is the budget, and it is the only term that grows linearly with chunk count. Everything else — BM25, fusion, and the per-id fetch — is a small, roughly fixed floor.

Because the cosine scan is linear in corpus size and the rest is fixed, retrieval latency grows predictably. Extrapolating from the measured 1,000-chunk point: ~10,000 chunks lands in the tens of milliseconds, and ~100,000 chunks in the low hundreds of milliseconds. Those projections are extrapolation from the one measured point, not measurements in their own right; the scan slice grows with the corpus while the floor stays put.

## Why brute force, not an ANN index

The cosine scan dominates the budget, so the natural question is whether an approximate-nearest-neighbour index would be faster. It is not, at this scale. Measured over the same 1,000-chunk index with the DuckDB `vss` extension (cosine metric, experimental-persistence flag), against the brute-force exact top-k as ground truth:

| Approach | Vector-ranking p50 | Recall@5 |
| --- | --- | --- |
| Brute-force exact scan | ~30.1 ms | 1.00 |
| HNSW index (vss extension) | ~29.6 ms | 0.96 mean, 0.80 worst-case |

The planner did use the index — `HNSW_INDEX_SCAN` was confirmed in the query plan — so this is a genuine HNSW path. The result is that HNSW is ~2% faster, within measurement noise, while giving up exact recall (0.96 mean, down to 0.80 on the worst query). At the target scale the number of distance comparisons is small enough that an exact scan is already fast, so the index trades exact recall for no speed. HNSW also needs the experimental-persistence flag, which fights the read-only baked-index model. HNSW only pays off far beyond this niche, where the distance-comparison count itself dominates the budget. The schema stays HNSW-ready — a fixed-size ARRAY column — so anyone who outgrows the niche can opt in, but the default is exact brute force.

## Choosing a model and dimension

The cosine scan is ~80% of query time and grows with __dimension x chunk count__: every scanned chunk computes a distance over the full vector, so halving the dimension roughly halves the scan's per-chunk work, and smaller vectors also shrink the DuckDB file linearly. Model choice is therefore a direct lever on both latency and storage, not just relevance.

The default is `fastembed:minishlab/potion-multilingual-128M`, a 256-dimension static [Model2Vec](https://github.com/MinishLab/model2vec) model. "Static" means it maps each token to a fixed vector and averages — there is no transformer forward pass — so it is the fastest and smallest option and serves international documentation out of the box. The tradeoff is relevance: a static model has no contextual understanding, so a contextual (transformer) model such as the MiniLM, mpnet, or e5 tiers will rank better on nuanced or ambiguous queries, at the cost of a larger vector, a larger DuckDB file, and a slower scan. Pick the smallest dimension that meets your relevance bar; 256 dims keep the file smallest while covering multiple languages, which is why it is the default.

Measured candidate models (hardware: MacBook Pro M1 (2021); embed p50 over 20 iterations; `hit@k` is a small fixed multilingual query/doc relevance sanity check, not a full benchmark; produced by `scripts/benchmark_embedders.py`):

| Model | Dimensions | Embed p50 | hit@1 | hit@3 |
| --- | --- | --- | --- | --- |
| `fastembed:minishlab/potion-multilingual-128M` (default) | 256 | 0.1 ms | 0.80 | 1.00 |
| `fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384 | 4.2 ms | 0.80 | 1.00 |
| `fastembed:sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | 768 | 11.2 ms | 0.80 | 1.00 |
| `fastembed:intfloat/multilingual-e5-large` | 1024 | 35.6 ms | 0.80 | 1.00 |
| `fastembed:BAAI/bge-small-en-v1.5` | 384 | 4.0 ms | 0.80 | 1.00 |
| `fastembed:BAAI/bge-large-en-v1.5` | 1024 | 35.2 ms | 1.00 | 1.00 |
| `hf:onnx-community/Qwen3-Embedding-0.6B-ONNX` | 1024 | 44.1 ms | 1.00 | 1.00 |
| `hf:onnx-community/embeddinggemma-300m-ONNX` | 768 | ~23 ms | 0.80 | 1.00 |

The two `hf:` rows are real measurements through the generalized loader. Qwen3-Embedding is a decoder-style, external-data export: the loader co-locates the external weight file, synthesises `position_ids`, feeds an empty key/value cache, and reads the hidden-state output. At 1,024 dims and ~44 ms it is the slowest and largest tier on offer — on par with the other 1,024-dim contextual models — yet it is the only candidate to score `hit@1` 1.00 on the tiny multilingual relevance bank. EmbeddingGemma is a full-pipeline export: the graph bakes pooling and dense projection heads in and exposes a final `sentence_embedding` output, which the loader reads directly rather than re-pooling the backbone (reading the raw backbone instead drops `hit@1` to 0.60 — the dense heads carry real signal). At 768 dims it is a lighter, faster point on the curve (~20 ms, CPU-variable) that matches the fastembed tiers' `hit@1` 0.80 here. The static 256-dim default still embeds in ~0.1 ms — two orders of magnitude faster — and the relevance bank is a sanity check, not a full benchmark; measure on your own corpus before trading the default's size and speed for a larger model.

## Deliberate omissions

Two things the package deliberately leaves out. They are scope choices that buy its frugality, not missing features.

__No stemmed, multi-field full-text engine and no second-stage re-ranker on the lexical side.__ The design relies on the embedding model as the primary relevance signal. The default embedding model already handles paraphrase and synonyms across languages with multilingual-capable semantic vectors, so a heavyweight stemmed and stopword-aware FTS layer would add cost and tie the index to one language without improving relevance. Semantic embeddings supersede a classical FTS stack. The lexical signal is therefore kept deliberately minimal: a language-agnostic exact-token BM25 with no stemming and no stopwords, present only to catch what vectors miss — acronyms, codes, and identifiers (so a query for `ESRS` finds `ESRS`). That thin exact-token guard is the whole lexical layer, and it is intentional.

__No re-ranking stage after chunk selection.__ Retrieval returns the fused top-k directly; there is no cross-encoder or LLM re-ranker re-scoring the selected chunks. A re-ranker adds a second model — and, if remote, a network hop — and latency, for a modest relevance gain that the hybrid vector + exact-token fusion already covers well enough for the target use case. The measurement backs this up: fusion itself is ~0 ms, and the point of the package is to answer in tens of milliseconds in-process, which a re-ranker would undo. A harness that needs re-ranking is free to add its own; the package does not impose that cost on everyone.

## In-process versus a network vector database

In-process retrieval is fixed and local: ~41 ms p50 at 1,000 chunks on the reference machine, with no network hop, because the index is a file in the agent's container queried by a local function call.

A hosted vector database pays its own compute plus a network round-trip to reach it. That round-trip is an estimated ~10-50 ms same-region, and 50-150 ms+ cross-region or on a cold connection, with a long tail that in-process execution does not have. The remote store adds this round-trip on top of its own search work, and none of the added time does any more searching.

The boundary is honest: at the thousands-to-low-tens-of-thousands niche the network hop roughly doubles end-to-end latency and buys nothing. A remote store only pays for itself once the corpus outgrows what a brute-force scan serves well.

This is the sweet spot for `duckdb-vectors`: integrating content of significant size — thousands of chunks — directly into the agent, trading a managed vector service for a file in the image and saving the per-query network latency. Below that scale it is both faster and simpler; above it, reach for the served store.

For the API and indexing workflow, see the [user guide](duckdb-vectors-guide); for the schema and internals, see the [technical design](duckdb-vectors-primitive).
