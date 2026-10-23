"""Measure query latency and the data-volume scan curve for duckdb-vectors.

Builds real indexes at a range of chunk counts and times warm ``query()`` calls,
reporting p50/p95 so the numbers in ``documentation/performance-guide.md`` can be
replaced by measured percentiles rather than an architecture-derived model.

The harness separates the two slices the guide's cost model names: ``embed`` (the
query-embedding call) and ``scan`` (the hybrid vector + BM25 ranking over the
DuckDB file). It times each independently and the full ``query()`` so the report
mirrors the model's decomposition.

Backends:

- ``fastembed:minishlab/potion-multilingual-128M`` (default) — fastembed ONNX,
  256-dim multilingual, no network. The realistic serving path for the embed
  timing.
- ``hashing`` — deterministic, dependency-free, no network. Use it to isolate the
  scan curve at large chunk counts without paying embedding cost or time.
- ``bedrock`` / ``bedrock:titan`` — opt-in only. It issues a billable Bedrock
  ``invoke_model`` round-trip per query and needs AWS credentials; it is never the
  default. Expect its embed slice to dominate, exactly as the guide predicts.

Run it from the repository root with the project venv:

    $ .venv/bin/python scripts/benchmark_performance.py
    $ .venv/bin/python scripts/benchmark_performance.py --sizes 1000 10000 50000
    $ .venv/bin/python scripts/benchmark_performance.py --json results.json
    $ .venv/bin/python scripts/benchmark_performance.py --backend bedrock:titan
    $ .venv/bin/python scripts/benchmark_performance.py --backend hashing

Nothing here is wired into the test suite: it builds large indexes and (for the
bedrock backend) makes billable calls, so it is a manual utility, not a test.
"""

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from duckdb_vectors import Chunk, VectorStore  # noqa: E402

DEFAULT_SIZES = (1_000, 10_000, 50_000, 100_000)
DEFAULT_WARMUP = 3
DEFAULT_ITERATIONS = 30
# A small bank of queries, cycled so no single string dominates the timing.
QUERIES = (
    "how does an embedded database answer a search?",
    "what is the cold start cost of a local embedding model?",
    "how much data fits in a single container image?",
    "explain hybrid vector and exact-token retrieval",
    "when should I reach for a managed vector database instead?",
)


def _synthetic_chunk(index: int) -> Chunk:
    """Build one ~300-word chunk, matching the markdown chunker's target size.

    The text varies per index so vectors differ and the FTS index holds real
    tokens, keeping the scan representative rather than degenerate.
    """
    words = [f"token{index}_{position}" for position in range(300)]
    return Chunk(
        chunk_id=f"chunk-{index}",
        text=" ".join(words),
        source=f"source-{index % 50}.md",
        position=index,
        title=f"Section {index}",
    )


def _build_index(db_path: str, backend: str, size: int, batch: int = 2_000) -> None:
    """Populate a store with ``size`` synthetic chunks, embedding in batches."""
    store = VectorStore(db_path=db_path, backend=backend)
    try:
        for start in range(0, size, batch):
            chunks = [
                _synthetic_chunk(index)
                for index in range(start, min(start + batch, size))
            ]
            store.put_vectors(chunks)
    finally:
        store.close()


def _percentiles(samples: list[float]) -> dict[str, float]:
    """Return p50/p95/mean in milliseconds for a list of second-valued samples."""
    ordered = sorted(samples)
    quantiles = statistics.quantiles(ordered, n=100, method="inclusive")
    return {
        "p50_ms": statistics.median(ordered) * 1_000,
        "p95_ms": quantiles[94] * 1_000,
        "mean_ms": statistics.fmean(ordered) * 1_000,
    }


def _time_calls(store: VectorStore, warmup: int, iterations: int) -> dict:
    """Time the embed slice and the full query over a cycled query bank."""
    for i in range(warmup):
        store.query(QUERIES[i % len(QUERIES)], top_k=5)

    embed_samples: list[float] = []
    query_samples: list[float] = []
    for i in range(iterations):
        text = QUERIES[i % len(QUERIES)]

        start = time.perf_counter()
        store.embedder.embed([text])
        embed_samples.append(time.perf_counter() - start)

        start = time.perf_counter()
        store.query(text, top_k=5)
        query_samples.append(time.perf_counter() - start)

    embed = _percentiles(embed_samples)
    full = _percentiles(query_samples)
    scan_p50 = max(full["p50_ms"] - embed["p50_ms"], 0.0)
    return {"embed": embed, "query": full, "scan_p50_ms": scan_p50}


def _db_size_mb(db_path: str) -> float:
    path = Path(db_path)
    return path.stat().st_size / (1024 * 1024) if path.exists() else 0.0


def benchmark(
    sizes: tuple[int, ...],
    backend: str,
    warmup: int,
    iterations: int,
) -> list[dict]:
    """Build an index per size and time warm queries against it."""
    rows: list[dict] = []
    for size in sizes:
        with tempfile.TemporaryDirectory(prefix="ddbv_bench_") as directory:
            db_path = str(Path(directory) / f"bench_{size}.duckdb")
            print(f"building {size:>8,} chunks ({backend}) ...", flush=True)
            _build_index(db_path, backend, size)
            size_mb = _db_size_mb(db_path)

            store = VectorStore(db_path=db_path, backend=backend, read_only=True)
            try:
                timings = _time_calls(store, warmup, iterations)
            finally:
                store.close()

            rows.append(
                {
                    "chunks": size,
                    "backend": backend,
                    "dimensions": store.dimensions,
                    "db_size_mb": round(size_mb, 1),
                    **timings,
                }
            )
            last = rows[-1]
            print(
                f"  dim={last['dimensions']} db={last['db_size_mb']} MB "
                f"embed_p50={last['embed']['p50_ms']:.1f}ms "
                f"query_p50={last['query']['p50_ms']:.1f}ms "
                f"query_p95={last['query']['p95_ms']:.1f}ms",
                flush=True,
            )
    return rows


def _markdown_table(rows: list[dict]) -> str:
    """Render the results as a pipe table matching the docs house style."""
    header = (
        "| Chunks | DB size | Embed p50 | Scan p50 | Query p50 | Query p95 |\n"
        "| --- | --- | --- | --- | --- | --- |"
    )
    lines = [header]
    for row in rows:
        lines.append(
            f"| {row['chunks']:,} "
            f"| ~{row['db_size_mb']} MB "
            f"| {row['embed']['p50_ms']:.1f} ms "
            f"| {row['scan_p50_ms']:.1f} ms "
            f"| {row['query']['p50_ms']:.1f} ms "
            f"| {row['query']['p95_ms']:.1f} ms |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_SIZES),
        help="chunk counts to benchmark (default: 1000 10000 50000 100000)",
    )
    parser.add_argument(
        "--backend",
        default="fastembed:minishlab/potion-multilingual-128M",
        help=(
            "embedding backend: fastembed:<model> (default), hashing, "
            "hf:<repo>, bedrock[:titan]"
        ),
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help="warm-up queries discarded before timing (default: 3)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help="timed queries per size (default: 30)",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="also write the raw results as JSON to PATH",
    )
    args = parser.parse_args(argv)

    if args.backend.startswith("bedrock"):
        print(
            "note: the bedrock backend issues billable Bedrock calls and needs "
            "AWS credentials.",
            flush=True,
        )

    rows = benchmark(
        sizes=tuple(args.sizes),
        backend=args.backend,
        warmup=args.warmup,
        iterations=args.iterations,
    )

    print(f"\nMeasured results (warm query, {args.iterations} iterations):\n")
    print(_markdown_table(rows))

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote raw results to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
