"""Run tagged-retrieval conditions over a dataset and write a JSON result.

Conditions, all over the same chunks and queries:

* ``baseline``: no tags anywhere.
* ``tags_embedding``: tags in front of the embedded text only.
* ``tags_bm25``: raw embeddings, tags matched by the keyword index only.
* ``tags_both``: tags in the embedding and in the keyword index.

Each condition is searched vector-only and hybrid, at several ``top_k``. Scores
follow the Anthropic cookbook (see :mod:`.metrics`). Every other condition is
compared with the baseline by paired bootstrap on the per-query score and by an
exact McNemar test on "all golden chunks found".

Run from the repository root:

    $ .venv/bin/python -m benchmarks.tagged_retrieval.run \\
        --tag-fields "repo: $.repo_name" --tag-fields "directory: $.directory"
"""

import argparse
import datetime
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from benchmarks.tagged_retrieval import metrics
from benchmarks.tagged_retrieval.datasets import Dataset, load_anthropic
from duckdb_vectors.config import Settings
from duckdb_vectors.etag import compute_etag
from duckdb_vectors.store import VectorStore

DEFAULT_BACKEND = "fastembed:minishlab/potion-multilingual-128M"
DEFAULT_KS = (5, 10, 20)
CONDITIONS = ("baseline", "tags_embedding", "tags_bm25", "tags_both")
_REPO = Path(__file__).resolve().parents[2]
_CACHE = Path(__file__).resolve().parent / ".cache"
_RESULTS = Path(__file__).resolve().parent / "results"


def build_store(
    dataset: Dataset,
    db_path: Path,
    backend: str,
    condition: str,
    tag_fields: list[str],
) -> VectorStore:
    """Index the dataset's chunks under one condition."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}")
    tagged = condition != "baseline"
    settings = Settings(
        db_path=str(db_path),
        backend=backend,
        tag_fields=tag_fields if tagged else [],
        tag_bm25=condition in ("tags_bm25", "tags_both"),
    )
    store = VectorStore(settings=settings)
    chunks = dataset.chunks
    if condition == "tags_bm25":
        # Keyword index sees the tags, the embedding does not: embed the raw
        # text and write the rows ourselves, with the tag column filled.
        vectors = store.embedder.embed([chunk.text for chunk in chunks])
        rows = [
            (
                chunk,
                compute_etag(
                    chunk.text, store.embedder.model_identifier, store.dimensions
                ),
                vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        store._write_rows(rows, datetime.datetime.now(datetime.UTC))
    else:
        store.put_vectors(chunks)
    store.close()
    return store


def evaluate(
    store: VectorStore, dataset: Dataset, ks: tuple[int, ...], hybrid: bool
) -> dict:
    """Per-query scores and latency at each ``k`` for one search mode."""
    result: dict = {}
    for k in ks:
        scores, reciprocal, passes, timings = [], [], [], []
        for query in dataset.queries:
            started = time.perf_counter()
            hits = store.query(query.text, top_k=k, hybrid=hybrid)
            timings.append((time.perf_counter() - started) * 1000)
            retrieved = [hit.chunk.chunk_id for hit in hits]
            score = metrics.score_query(retrieved, query.golden)
            scores.append(score)
            passes.append(score == 1.0)
            reciprocal.append(metrics.reciprocal_rank(retrieved, query.golden))
        result[str(k)] = {
            "pass": metrics.mean(scores),
            "failure": 1 - metrics.mean(scores),
            "mrr": metrics.mean(reciprocal),
            "p50_ms": statistics.median(timings),
            "scores": scores,
            "all_found": passes,
        }
    store.close()
    return result


def compare(baseline: dict, other: dict) -> dict:
    """Paired bootstrap and McNemar of ``other`` against ``baseline``."""
    out = {}
    for k, base in baseline.items():
        diff, low, high = metrics.paired_bootstrap(base["scores"], other[k]["scores"])
        gained, lost, p = metrics.mcnemar(base["all_found"], other[k]["all_found"])
        out[k] = {
            "pass_difference": diff,
            "ci95": [low, high],
            "queries_gained": gained,
            "queries_lost": lost,
            "mcnemar_p": p,
        }
    return out


def _git_state() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=_REPO, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "sha": run("rev-parse", "HEAD"),
        "dirty": bool(run("status", "--porcelain")),
    }


def run_benchmark(
    dataset: Dataset,
    backend: str,
    tag_fields: list[str],
    conditions: tuple[str, ...] = CONDITIONS,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> dict:
    """Run every condition in vector-only and hybrid mode."""
    results: dict = {}
    with tempfile.TemporaryDirectory() as directory:
        for condition in conditions:
            started = time.perf_counter()
            db = Path(directory) / f"{condition}.duckdb"
            store = build_store(dataset, db, backend, condition, tag_fields)
            index_seconds = time.perf_counter() - started
            store = VectorStore(settings=store.settings)
            results[condition] = {
                "index_seconds": index_seconds,
                "db_bytes": db.stat().st_size,
                "vector": evaluate(store, dataset, ks, hybrid=False),
                "hybrid": evaluate(store, dataset, ks, hybrid=True),
            }
    if "baseline" in results:
        for condition, outcome in results.items():
            if condition == "baseline":
                continue
            outcome["versus_baseline"] = {
                mode: compare(results["baseline"][mode], outcome[mode])
                for mode in ("vector", "hybrid")
            }
    return {
        "git": _git_state(),
        "dataset": {
            "name": dataset.name,
            "chunks": len(dataset.chunks),
            "queries": len(dataset.queries),
            "sources": dataset.sources,
        },
        "backend": backend,
        "tag_fields": tag_fields,
        "ks": list(ks),
        "conditions": results,
    }


def summarize(report: dict) -> str:
    """A short text table of Pass@k and failure@k per condition and mode."""
    ks = report["ks"]
    lines = [
        f"{report['dataset']['name']}  {report['backend']}  "
        f"tags={report['tag_fields']}",
        "condition        mode    " + "  ".join(f"pass@{k:<3}" for k in ks),
    ]
    for condition, outcome in report["conditions"].items():
        for mode in ("vector", "hybrid"):
            cells = "  ".join(f"{outcome[mode][str(k)]['pass']:.3f}  " for k in ks)
            lines.append(f"{condition:<16} {mode:<7} {cells}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--tag-fields", action="append", default=[])
    parser.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    parser.add_argument("--top-k", nargs="+", type=int, default=list(DEFAULT_KS))
    parser.add_argument("--limit-queries", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    dataset = load_anthropic(_CACHE)
    if args.limit_queries:
        dataset.queries = dataset.queries[: args.limit_queries]
    report = run_benchmark(
        dataset,
        args.backend,
        args.tag_fields,
        tuple(args.conditions),
        tuple(args.top_k),
    )
    _RESULTS.mkdir(exist_ok=True)
    name = args.backend.replace(":", "_").replace("/", "_")
    output = args.output or _RESULTS / f"{report['git']['sha'][:8]}-{name}.json"
    output.write_text(json.dumps(report, indent=1))
    print(summarize(report))
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
