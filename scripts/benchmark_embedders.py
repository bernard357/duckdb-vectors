"""Benchmark candidate embedding models for duckdb-vectors.

Measures, per candidate model: embed p50 latency, the produced vector
dimension, the on-disk model download size (read from the huggingface_hub
cache when determinable), and a small fixed relevance sanity check (a handful
of query/expected-doc pairs ranked by cosine, reported as hit@1 / hit@3). The
results are emitted as a markdown pipe table matching the docs house style.

Candidates span the ``fastembed:`` multilingual ladder (256/384/768/1024), the
English ``BAAI/bge`` tiers (384/1024), and one ``hf:`` model
(``onnx-community/Qwen3-Embedding-0.6B-ONNX``) exercising the core Hugging Face
ONNX loader on a decoder-style, external-data export.

Run it from the repository root with the project venv, offline where the models
are already cached:

    $ .venv/bin/python scripts/benchmark_embedders.py
    $ .venv/bin/python scripts/benchmark_embedders.py --json results.json

HONESTY: a model that cannot be downloaded or loaded in this environment is
reported as "not run — model not available in this environment" rather than
fabricating any latency, dimension, or hit@k number. Hardware label is fixed to
"MacBook Pro M1 (2021)".

Nothing here is wired into the test suite: it may download models and build
indexes, so it is a manual utility, not a test.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from duckdb_vectors.embedders.registry import resolve_embedder  # noqa: E402

HARDWARE = "MacBook Pro M1 (2021)"

CANDIDATES = (
    "fastembed:minishlab/potion-multilingual-128M",
    "fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "fastembed:sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
    "fastembed:intfloat/multilingual-e5-large",
    "fastembed:BAAI/bge-small-en-v1.5",
    "fastembed:BAAI/bge-large-en-v1.5",
    "hf:onnx-community/Qwen3-Embedding-0.6B-ONNX",
)

DEFAULT_WARMUP = 2
DEFAULT_ITERATIONS = 20

# A small, fixed relevance bank: each query's expected best document is the one
# at the matching index. Ranking all docs by cosine to the query gives hit@k.
RELEVANCE_PAIRS = (
    (
        "how does an embedded database answer a search?",
        "DuckDB runs queries in-process against a single local file.",
    ),
    (
        "what keeps a container image small?",
        "A static ONNX embedding model avoids a heavy torch dependency.",
    ),
    (
        "how are two rankings combined?",
        "Reciprocal rank fusion blends the vector and BM25 result lists.",
    ),
    (
        "comment fonctionne la recherche multilingue ?",
        "Un modèle multilingue encode plusieurs langues dans le même espace.",
    ),
    (
        "¿cómo se almacenan los vectores?",
        "Los vectores se guardan en una columna FLOAT de tamaño fijo.",
    ),
)

QUERIES = tuple(query for query, _ in RELEVANCE_PAIRS)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _relevance(embedder) -> tuple[float, float]:
    """Return (hit@1, hit@3) over the fixed relevance bank."""
    docs = [doc for _, doc in RELEVANCE_PAIRS]
    doc_vectors = embedder.embed(docs)
    query_vectors = embedder.embed(list(QUERIES))
    hits_1 = 0
    hits_3 = 0
    for i, query_vector in enumerate(query_vectors):
        scored = sorted(
            range(len(docs)),
            key=lambda j: _cosine(query_vector, doc_vectors[j]),
            reverse=True,
        )
        if scored and scored[0] == i:
            hits_1 += 1
        if i in scored[:3]:
            hits_3 += 1
    total = len(RELEVANCE_PAIRS)
    return hits_1 / total, hits_3 / total


def _embed_p50_ms(embedder, warmup: int, iterations: int) -> float:
    for i in range(warmup):
        embedder.embed([QUERIES[i % len(QUERIES)]])
    samples: list[float] = []
    for i in range(iterations):
        text = QUERIES[i % len(QUERIES)]
        start = time.perf_counter()
        embedder.embed([text])
        samples.append(time.perf_counter() - start)
    return statistics.median(samples) * 1_000


def _download_size_mb(spec: str) -> float | None:
    """Best-effort model download size from the huggingface_hub cache.

    Returns megabytes if the repo's cache directory can be located, else None.
    """
    _, _, repo_id = spec.partition(":")
    if "/" not in repo_id:
        return None
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except Exception:  # noqa: BLE001 - size is best-effort only
        return None
    cache_dir = Path(HF_HUB_CACHE)
    folder = "models--" + repo_id.replace("/", "--")
    repo_dir = cache_dir / folder
    if not repo_dir.exists():
        return None
    total = sum(p.stat().st_size for p in repo_dir.rglob("*") if p.is_file())
    return total / (1024 * 1024)


def benchmark(candidates: tuple[str, ...], warmup: int, iterations: int) -> list[dict]:
    rows: list[dict] = []
    for spec in candidates:
        print(f"benchmarking {spec} ...", flush=True)
        try:
            embedder = resolve_embedder(spec)
            p50 = _embed_p50_ms(embedder, warmup, iterations)
            dimension = embedder.dimensions
            hit_1, hit_3 = _relevance(embedder)
            size_mb = _download_size_mb(spec)
            rows.append(
                {
                    "model": spec,
                    "status": "ok",
                    "dimensions": dimension,
                    "embed_p50_ms": round(p50, 1),
                    "download_mb": round(size_mb, 1) if size_mb else None,
                    "hit@1": round(hit_1, 2),
                    "hit@3": round(hit_3, 2),
                }
            )
            print(
                f"  dim={dimension} embed_p50={p50:.1f}ms "
                f"hit@1={hit_1:.2f} hit@3={hit_3:.2f}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - honesty: record, do not fake
            rows.append(
                {
                    "model": spec,
                    "status": "not run — model not available in this environment",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"  not run: {type(exc).__name__}: {exc}", flush=True)
    return rows


def _cell(row: dict, key: str) -> str:
    if row["status"] != "ok":
        return "n/a"
    value = row.get(key)
    return "n/a" if value is None else str(value)


def _markdown_table(rows: list[dict]) -> str:
    """Render the results as a pipe table matching the docs house style."""
    header = (
        f"Hardware: {HARDWARE}\n\n"
        "| Model | Dimensions | Embed p50 | Download | hit@1 | hit@3 |\n"
        "| --- | --- | --- | --- | --- | --- |"
    )
    lines = [header]
    for row in rows:
        if row["status"] != "ok":
            lines.append(
                f"| `{row['model']}` | not run — model not available in this "
                "environment | | | | |"
            )
            continue
        download = (
            f"~{row['download_mb']} MB" if row["download_mb"] is not None else "n/a"
        )
        lines.append(
            f"| `{row['model']}` "
            f"| {row['dimensions']} "
            f"| {row['embed_p50_ms']} ms "
            f"| {download} "
            f"| {row['hit@1']} "
            f"| {row['hit@3']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        nargs="+",
        default=list(CANDIDATES),
        help="backend specs to benchmark (default: the full candidate list)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help="warm-up embeds discarded before timing (default: 2)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help="timed embeds per model (default: 20)",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="also write the raw results as JSON to PATH",
    )
    args = parser.parse_args(argv)

    rows = benchmark(tuple(args.models), args.warmup, args.iterations)

    print(f"\nMeasured results (embed p50, {args.iterations} iterations):\n")
    print(_markdown_table(rows))

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote raw results to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
