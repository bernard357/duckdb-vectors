"""Evaluate tagged retrieval on a prepared Markdown knowledge base.

The knowledge base is a folder of pages, possibly carrying page-level tags
(``kb_tags``) and section contexts (``kb_context``), and a validation set
(``kb_validation``, or a benchmark loader). Every condition indexes the same
chunks:

* ``baseline``: no tags.
* ``<set>:embedding``: the tag set in front of the embedded text.
* ``<set>:bm25``: the tag set in the keyword index only.
* ``<set>:both``: the tag set in the embedding and the keyword index.

A tag set is ``NAME=label: $.path;label: $.path``, for example
``context=context: $.retrieval.context``. Each condition is searched vector-only and
hybrid; one search at the largest ``k`` gives every smaller ``k``. Scores per
query: Pass@k (share of golden chunks found, as in the Anthropic cookbook),
failure@k, all-found, MRR and graded nDCG@k. Every condition is compared with
the baseline by a paired bootstrap interval and a sign-flip test, over all
queries and per stratum. The run writes one JSON file keyed by git SHA.

    $ make kb-evaluate KB=path/to/pages WORK=tmp/kb/work \\
        TAGS="context=context: $.retrieval.context"
"""

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from development import metrics
from development.curation import (
    CachedEmbedder,
    build_index,
    chunk_folder,
    read_json,
    write_json,
)
from development.paths import REPO_ROOT
from duckdb_vectors.embedders.registry import resolve_embedder
from duckdb_vectors.models import Chunk
from scripts.kb_validation import Item, ValidationSet

DEFAULT_BACKEND = "fastembed:minishlab/potion-multilingual-128M"
DEFAULT_KS = (5, 10, 20)
PLACEMENTS = ("embedding", "bm25", "both")


def parse_tag_set(spec: str) -> tuple[str, list[str]]:
    """``NAME=spec;spec`` into ``(NAME, [spec, ...])``."""
    name, equals, fields = spec.partition("=")
    items = [item.strip() for item in fields.split(";") if item.strip()]
    if not equals or not name.strip() or not items:
        raise ValueError(f"expected NAME=label: $.path[;...], got {spec!r}")
    return name.strip(), items


def conditions_for(tag_sets: dict[str, list[str]]) -> dict[str, tuple[list[str], str]]:
    """Condition name to ``(tag_fields, placement)``, baseline first."""
    conditions: dict[str, tuple[list[str], str]] = {"baseline": ([], "none")}
    for name, fields in tag_sets.items():
        for placement in PLACEMENTS:
            conditions[f"{name}:{placement}"] = (fields, placement)
    return conditions


def select_items(
    validation: ValidationSet, split: str, strata: list[str] | None
) -> list[Item]:
    """Valid items with golden chunks, in the split and strata asked for."""
    return [
        item
        for item in validation.items
        if item.valid
        and (item.golden or item.alternatives)
        and (split == "all" or item.split == split)
        and (not strata or item.stratum in strata)
    ]


def grades_of(item: Item) -> dict[str, int]:
    """Graded relevance: judged grades, or 1 for each native golden chunk."""
    judged = {c: g for c, g in item.grades.items() if g > 0}
    if judged and item.stratum != "native":
        return judged
    native = {c: 1 for c in item.golden}
    for group in item.alternatives:
        native.update({c: 1 for c in group})
    return native or judged


def score(item: Item, retrieved: list[str], k: int) -> dict:
    top = retrieved[:k]
    share = (
        metrics.score_alternatives(top, item.alternatives)
        if item.alternatives
        else metrics.score_query(top, item.golden)
    )
    wanted = item.golden or [c for group in item.alternatives for c in group]
    return {
        "pass": share,
        "all_found": share == 1.0,
        "rr": metrics.reciprocal_rank(top, wanted),
        "ndcg": metrics.ndcg(top, grades_of(item), k),
    }


def evaluate(store, items: list[Item], ks: tuple[int, ...], hybrid: bool) -> dict:
    """Per-query scores at every ``k`` from one search at the largest ``k``."""
    deepest = max(ks)
    per_k: dict[str, dict[str, list]] = {
        str(k): {"pass": [], "all_found": [], "rr": [], "ndcg": []} for k in ks
    }
    timings = []
    for item in items:
        started = time.perf_counter()
        hits = store.query(item.text, top_k=deepest, hybrid=hybrid)
        timings.append((time.perf_counter() - started) * 1000)
        retrieved = [hit.chunk.chunk_id for hit in hits]
        for k in ks:
            for key, value in score(item, retrieved, k).items():
                per_k[str(k)][key].append(value)
    timings.sort()
    return {
        "p50_ms": timings[len(timings) // 2] if timings else 0.0,
        "k": {
            k: {
                "pass": metrics.mean(values["pass"]),
                "failure": 1 - metrics.mean(values["pass"]),
                "mrr": metrics.mean(values["rr"]),
                "ndcg": metrics.mean(values["ndcg"]),
                "scores": values["pass"],
                "ndcg_scores": values["ndcg"],
                "all_found": values["all_found"],
            }
            for k, values in per_k.items()
        },
    }


def compare(base: list[float], other: list[float]) -> dict:
    diff, low, high = metrics.paired_bootstrap(base, other)
    return {
        "difference": diff,
        "ci95": [low, high],
        "p": metrics.sign_flip_p(base, other),
    }


def comparisons(results: dict, items: list[Item]) -> dict:
    """Every condition against the baseline, over all queries and per stratum."""
    strata = sorted({item.stratum for item in items})
    groups = {"all": list(range(len(items)))}
    for stratum in strata:
        groups[stratum] = [i for i, item in enumerate(items) if item.stratum == stratum]
    out: dict = {}
    for condition, outcome in results.items():
        if condition == "baseline":
            continue
        out[condition] = {}
        for mode in ("vector", "hybrid"):
            out[condition][mode] = {}
            for k, values in outcome[mode]["k"].items():
                base = results["baseline"][mode]["k"][k]
                out[condition][mode][k] = {}
                for group, indexes in groups.items():
                    if not indexes:
                        continue
                    pick = [values["scores"][i] for i in indexes]
                    ref = [base["scores"][i] for i in indexes]
                    base_failure = 1 - metrics.mean(ref)
                    other_failure = 1 - metrics.mean(pick)
                    out[condition][mode][k][group] = {
                        "queries": len(indexes),
                        "baseline_failure": base_failure,
                        "failure": other_failure,
                        "failure_reduction": (base_failure - other_failure)
                        / base_failure
                        if base_failure
                        else 0.0,
                        "pass": compare(ref, pick),
                        "ndcg": compare(
                            [base["ndcg_scores"][i] for i in indexes],
                            [values["ndcg_scores"][i] for i in indexes],
                        ),
                    }
    return out


def git_state() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "sha": run("rev-parse", "HEAD"),
        "dirty": bool(run("status", "--porcelain")),
    }


def run_evaluation(
    chunks: list[Chunk],
    items: list[Item],
    backend: str,
    tag_sets: dict[str, list[str]],
    ks: tuple[int, ...] = DEFAULT_KS,
    embedder=None,
) -> dict:
    """Index and search every condition; returns the report without metadata."""
    embedder = embedder or CachedEmbedder(resolve_embedder(backend))
    results: dict = {}
    with tempfile.TemporaryDirectory() as directory:
        for index, (condition, (fields, placement)) in enumerate(
            conditions_for(tag_sets).items()
        ):
            started = time.perf_counter()
            store = build_index(
                chunks,
                Path(directory) / f"{index}.duckdb",
                backend,
                tag_fields=fields,
                tag_bm25=placement in ("bm25", "both"),
                embedder=embedder,
                embed_tags=placement in ("embedding", "both"),
            )
            results[condition] = {
                "index_seconds": time.perf_counter() - started,
                "vector": evaluate(store, items, ks, hybrid=False),
                "hybrid": evaluate(store, items, ks, hybrid=True),
            }
            store.close()
    return {"conditions": results, "versus_baseline": comparisons(results, items)}


def summary(report: dict, k: str = "20") -> str:
    """Failure@k and its reduction per condition, hybrid and vector-only."""
    lines = [f"{'condition':24} {'mode':7} {'failure@' + k:>11} {'reduction':>10}"]
    for condition, outcome in report["conditions"].items():
        for mode in ("vector", "hybrid"):
            failure = outcome[mode]["k"][k]["failure"]
            versus = report["versus_baseline"].get(condition, {})
            reduction = versus.get(mode, {}).get(k, {}).get("all", {})
            text = f"{reduction['failure_reduction']:.0%}" if reduction else ""
            lines.append(f"{condition:24} {mode:7} {failure:11.3f} {text:>10}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--work", type=Path, default=Path("tmp/kb"))
    parser.add_argument("--validation", type=Path, default=None)
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--tags", action="append", default=[], help="NAME=spec;spec")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--strata", nargs="+", default=None)
    parser.add_argument("--top-k", nargs="+", type=int, default=list(DEFAULT_KS))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    validation = ValidationSet.model_validate(
        read_json(args.validation or args.work / "validation.json")
    )
    items = select_items(validation, args.split, args.strata)
    if not items:
        parser.error("no valid items with golden chunks in that split")
    tag_sets = dict(parse_tag_set(spec) for spec in args.tags)
    chunks = chunk_folder(args.folder)
    report = run_evaluation(chunks, items, args.backend, tag_sets, tuple(args.top_k))
    report = {
        "git": git_state(),
        "corpus": validation.corpus,
        "backend": args.backend,
        "split": args.split,
        "split_digest": validation.split.get("digest"),
        "strata": sorted({item.stratum for item in items}),
        "queries": len(items),
        "chunks": len(chunks),
        "tag_sets": tag_sets,
        "items": [item.id for item in items],
        "item_strata": [item.stratum for item in items],
        **report,
    }
    name = args.backend.replace(":", "_").replace("/", "_")
    output = args.output or (
        args.work
        / "results"
        / f"{report['git']['sha'][:8]}-{validation.corpus}-{name}-{args.split}.json"
    )
    write_json(output, report)
    print(summary(report))
    print(f"wrote {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
