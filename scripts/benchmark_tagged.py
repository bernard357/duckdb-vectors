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

    $ .venv/bin/python -m scripts.benchmark_tagged \\
        --tag-fields "repo: $.repo_name" --tag-fields "directory: $.directory"
"""

import argparse
import datetime
import json
import random
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel

from development import metrics
from development.datasets import LOADERS, Dataset
from development.paths import BENCHMARK_CACHE, BENCHMARK_RESULTS, REPO_ROOT
from duckdb_vectors.config import Settings
from duckdb_vectors.embedders.registry import resolve_embedder
from duckdb_vectors.etag import compute_etag
from duckdb_vectors.store import VectorStore

DEFAULT_BACKEND = "fastembed:minishlab/potion-multilingual-128M"
DEFAULT_KS = (5, 10, 20)
COST_SAMPLE = 100
CONDITIONS = ("baseline", "tags_embedding", "tags_bm25", "tags_both")
_REPO = REPO_ROOT
_CACHE = BENCHMARK_CACHE
_RESULTS = BENCHMARK_RESULTS


class CachedEmbedder:
    """Embeds each distinct text once per run and shares it across conditions.

    Conditions overlap heavily: the baseline and the keyword-only condition
    embed the same raw text, and every search repeats its query for each ``k``.
    The vectors are exactly the ones the wrapped embedder returns.
    """

    def __init__(self, embedder) -> None:
        self._embedder = embedder
        self._cache: dict[str, list[float]] = {}

    @property
    def model_identifier(self) -> str:
        return self._embedder.model_identifier

    @property
    def dimensions(self) -> int:
        return self._embedder.dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if missing:
            for text, vector in zip(
                missing, self._embedder.embed(missing), strict=True
            ):
                self._cache[text] = vector
        return [self._cache[text] for text in texts]


class Plan(BaseModel):
    """How one condition indexes the chunks."""

    tag_fields: list[str]
    embed: str  # "raw", "prefix" or "suffix"
    bm25: bool


def label_of(spec: str) -> str:
    """The label of a ``label: $.path`` tag spec."""
    return spec.partition(":")[0].strip()


def plan_condition(condition: str, tag_fields: list[str]) -> Plan:
    """Translate a condition name into an indexing plan.

    ``without:<label>`` is ``tags_both`` with one tag removed, the
    leave-one-tag-out ablation. ``tags_suffix`` puts the tags after the chunk
    text in the embedded string (a benchmark-only ablation of placement).
    """
    if condition.startswith("without:"):
        label = condition.partition(":")[2]
        if label not in {label_of(spec) for spec in tag_fields}:
            raise ValueError(f"no tag labelled {label!r} to leave out")
        kept = [spec for spec in tag_fields if label_of(spec) != label]
        return Plan(tag_fields=kept, embed="prefix", bm25=True)
    plans = {
        "baseline": Plan(tag_fields=[], embed="raw", bm25=False),
        "tags_embedding": Plan(tag_fields=tag_fields, embed="prefix", bm25=False),
        "tags_bm25": Plan(tag_fields=tag_fields, embed="raw", bm25=True),
        "tags_both": Plan(tag_fields=tag_fields, embed="prefix", bm25=True),
        "tags_suffix": Plan(tag_fields=tag_fields, embed="suffix", bm25=False),
    }
    if condition not in plans:
        raise ValueError(f"unknown condition {condition!r}")
    return plans[condition]


def reference_of(condition: str) -> str | None:
    """The condition a condition is compared with besides the baseline."""
    if condition.startswith("without:"):
        return "tags_both"
    return {"tags_suffix": "tags_embedding"}.get(condition)


def embedded_texts(store: VectorStore, chunks: list, plan: Plan) -> list[str]:
    """The text each chunk is embedded as under ``plan``."""
    if plan.embed == "prefix":
        return store.embedded_texts(chunks)
    if plan.embed == "suffix":
        prefixes = store.tag_prefixes(chunks)
        return [
            f"{chunk.text}\n\n{prefix}" if prefix else chunk.text
            for chunk, prefix in zip(chunks, prefixes, strict=True)
        ]
    return [chunk.text for chunk in chunks]


def cold_embed_ms(store: VectorStore, chunks: list, plan: Plan, backend: str) -> float:
    """Milliseconds per chunk to embed a sample of this condition's texts.

    Measured with a fresh embedder, because the run shares vectors between
    conditions and so cannot time them. Longer prefixes cost more tokens.
    """
    sample = chunks[:: max(1, len(chunks) // COST_SAMPLE)][:COST_SAMPLE]
    texts = embedded_texts(store, sample, plan)
    fresh = resolve_embedder(backend)
    fresh.embed(texts[:1])  # load the model before timing
    started = time.perf_counter()
    fresh.embed(texts)
    return (time.perf_counter() - started) * 1000 / len(texts)


def cold_query_embed_ms(dataset: Dataset, backend: str) -> float:
    """Milliseconds to embed one query with a fresh embedder.

    Search timings in a run exclude this: the embedder is shared, so a repeated
    query costs nothing. Add it to ``p50_ms`` for the latency a caller sees.
    """
    texts = [query.text for query in dataset.queries[:COST_SAMPLE]]
    fresh = resolve_embedder(backend)
    fresh.embed(texts[:1])
    started = time.perf_counter()
    for text in texts:
        fresh.embed([text])
    return (time.perf_counter() - started) * 1000 / len(texts)


def build_store(
    dataset: Dataset,
    db_path: Path,
    backend: str,
    condition: str,
    tag_fields: list[str],
    embedder=None,
) -> VectorStore:
    """Index the dataset's chunks under one condition."""
    plan = plan_condition(condition, tag_fields)
    settings = Settings(
        db_path=str(db_path),
        backend=backend,
        tag_fields=plan.tag_fields,
        tag_bm25=plan.bm25,
    )
    store = VectorStore(settings=settings, embedder=embedder)
    chunks = dataset.chunks
    texts = embedded_texts(store, chunks, plan)
    vectors = store.embedder.embed(texts)
    rows = [
        (
            chunk,
            compute_etag(text, store.embedder.model_identifier, store.dimensions),
            vector,
        )
        for chunk, text, vector in zip(chunks, texts, vectors, strict=True)
    ]
    store._write_rows(rows, datetime.datetime.now(datetime.UTC))
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
            score = (
                metrics.score_alternatives(retrieved, query.alternatives)
                if query.alternatives
                else metrics.score_query(retrieved, query.golden)
            )
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
    sample: dict | None = None,
) -> dict:
    """Run every condition in vector-only and hybrid mode."""
    results: dict = {}
    embedder = CachedEmbedder(resolve_embedder(backend))
    with tempfile.TemporaryDirectory() as directory:
        for condition in conditions:
            started = time.perf_counter()
            db = Path(directory) / f"{condition}.duckdb"
            store = build_store(dataset, db, backend, condition, tag_fields, embedder)
            index_seconds = time.perf_counter() - started
            store = VectorStore(settings=store.settings, embedder=embedder)
            plan = plan_condition(condition, tag_fields)
            results[condition] = {
                "index_seconds": index_seconds,
                "embed_ms_per_chunk": cold_embed_ms(
                    store, dataset.chunks, plan, backend
                ),
                "db_bytes": db.stat().st_size,
                "vector": evaluate(store, dataset, ks, hybrid=False),
                "hybrid": evaluate(store, dataset, ks, hybrid=True),
            }
    for condition, outcome in results.items():
        for key, other in (("versus_baseline", "baseline"), ("versus_reference", None)):
            other = other or reference_of(condition)
            if other is None or other == condition or other not in results:
                continue
            outcome[key] = {
                "against": other,
                **{
                    mode: compare(results[other][mode], outcome[mode])
                    for mode in ("vector", "hybrid")
                },
            }
    return {
        "git": _git_state(),
        "dataset": {
            "name": dataset.name,
            "chunks": len(dataset.chunks),
            "queries": len(dataset.queries),
            "sources": dataset.sources,
            "quality": dataset.quality,
            "query_sample": sample,
        },
        "backend": backend,
        "query_embed_ms": cold_query_embed_ms(dataset, backend),
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
            lines.append(f"{condition:<22} {mode:<7} {cells}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset", choices=sorted(LOADERS), default="anthropic")
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--tag-fields", action="append", default=[])
    parser.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    parser.add_argument("--top-k", nargs="+", type=int, default=list(DEFAULT_KS))
    parser.add_argument("--limit-queries", type=int, default=None)
    parser.add_argument(
        "--sample-queries",
        type=int,
        default=None,
        help="use a seeded random sample of this many queries",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="add a without:<label> condition per tag (leave one tag out)",
    )
    parser.add_argument(
        "--suffix",
        action="store_true",
        help="add the tags_suffix condition (tags after the chunk text)",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    dataset = LOADERS[args.dataset](_CACHE)
    tag_fields = args.tag_fields or dataset.default_tag_fields
    sample = None
    if args.limit_queries:
        dataset.queries = dataset.queries[: args.limit_queries]
    if args.sample_queries and args.sample_queries < len(dataset.queries):
        total = len(dataset.queries)
        picked = sorted(
            random.Random(args.seed).sample(range(total), args.sample_queries)
        )
        dataset.queries = [dataset.queries[i] for i in picked]
        sample = {"size": args.sample_queries, "of": total, "seed": args.seed}
    conditions = list(args.conditions)
    if args.suffix:
        conditions.append("tags_suffix")
    if args.ablate:
        conditions += [f"without:{label_of(spec)}" for spec in tag_fields]
    report = run_benchmark(
        dataset,
        args.backend,
        tag_fields,
        tuple(conditions),
        tuple(args.top_k),
        sample,
    )
    _RESULTS.mkdir(parents=True, exist_ok=True)
    name = args.backend.replace(":", "_").replace("/", "_")
    stem = f"{report['git']['sha'][:8]}-{dataset.name}-{name}"
    if sample:
        stem += f"-q{sample['size']}"
    if args.ablate or args.suffix:
        stem += "-ablation"
    output = args.output or _RESULTS / f"{stem}.json"
    output.write_text(json.dumps(report, indent=1))
    print(summarize(report))
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
