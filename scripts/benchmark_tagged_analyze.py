"""Turn result files into the tables and verdicts of the tagged retrieval study.

Reads every file in ``tmp/benchmarks/results/``, keeps the latest for each
dataset, embedder, sample and ablation setting, and prints Markdown tables:

* gain of each condition over the baseline, per dataset and embedder (H1, H2, H3);
* the cost of tagging: embedding time, file size, query time;
* the leave-one-tag-out effect of each tag next to its corpus statistics (H5);
* the share of an LLM contextual header's gain that tags recover (H4).

All differences are in Pass@k on the same queries, with a 95% paired bootstrap
interval computed here from the stored per-query scores.

    $ .venv/bin/python -m scripts.benchmark_tagged_analyze
"""

import argparse
import json
import tempfile
from pathlib import Path

from development import metrics
from development.datasets import LOADERS
from development.paths import BENCHMARK_CACHE, BENCHMARK_RESULTS
from duckdb_vectors.config import Settings
from duckdb_vectors.store import VectorStore
from duckdb_vectors.tag_stats import compute_tag_report

_RESULTS = BENCHMARK_RESULTS
MODE = "hybrid"
K = "20"
SHORT = {
    "minishlab/potion-multilingual-128M": "potion-256",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": "MiniLM-384",
    "BAAI/bge-small-en-v1.5": "bge-small-384",
    "intfloat/multilingual-e5-large": "e5-large-1024",
    "BAAI/bge-large-en-v1.5": "bge-large-1024",
}


def load_results(directory: Path = _RESULTS) -> dict[tuple, dict]:
    """Latest result per ``(dataset, backend, sampled, ablation)``."""
    found: dict[tuple, tuple[float, dict]] = {}
    for path in directory.glob("*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        conditions = report["conditions"]
        key = (
            report["dataset"]["name"],
            report["backend"],
            bool(report["dataset"].get("query_sample")),
            any(c.startswith("without:") or c == "tags_suffix" for c in conditions),
        )
        stamp = path.stat().st_mtime
        if key not in found or stamp > found[key][0]:
            found[key] = (stamp, report)
    return {key: report for key, (_, report) in found.items()}


def short(backend: str) -> str:
    return SHORT.get(backend.partition(":")[2], backend)


def scores(report: dict, condition: str, mode: str = MODE, k: str = K) -> list[float]:
    return report["conditions"][condition][mode][k]["scores"]


def delta(report: dict, condition: str, other: str, mode: str = MODE, k: str = K):
    """``(difference, low, high, mcnemar_p)`` of ``condition`` over ``other``."""
    a, b = scores(report, other, mode, k), scores(report, condition, mode, k)
    diff, low, high = metrics.paired_bootstrap(a, b, resamples=5000)
    base = report["conditions"][other][mode][k]["all_found"]
    cond = report["conditions"][condition][mode][k]["all_found"]
    p = metrics.mcnemar(base, cond)[2]
    return diff, low, high, p


def fmt(diff: float, low: float, high: float, p: float) -> str:
    mark = " *" if low > 0 or high < 0 else ""
    return f"{diff:+.3f} [{low:+.3f}, {high:+.3f}]{mark}"


def gains_table(results: dict, mode: str = MODE, k: str = K) -> str:
    """Pass@k of the baseline and each condition's gain, per dataset and embedder."""
    rows = [
        "| Dataset | Embedder | Queries | Baseline | Prefix only | Keyword only "
        "| Both |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (dataset, backend, _, _), report in sorted(results.items()):
        if (
            "baseline" not in report["conditions"]
            or "tags_both" not in report["conditions"]
        ):
            continue
        base = report["conditions"]["baseline"][mode][k]["pass"]
        cells = []
        for condition in ("tags_embedding", "tags_bm25", "tags_both"):
            if condition in report["conditions"]:
                cells.append(fmt(*delta(report, condition, "baseline", mode, k)))
            else:
                cells.append("")
        rows.append(
            f"| {dataset} | {short(backend)} | {report['dataset']['queries']} "
            f"| {base:.3f} | " + " | ".join(cells) + " |"
        )
    return "\n".join(rows)


def cost_table(results: dict) -> str:
    """Embedding time, file size and search time of baseline versus tags_both."""
    rows = [
        "| Dataset | Embedder | Embed ms/chunk (base) | (tags) | File MB (base) "
        "| (tags) | Search ms p50 | Query embed ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (dataset, backend, _, _ablation), report in sorted(results.items()):
        conds = report["conditions"]
        if "baseline" not in conds or "tags_both" not in conds:
            continue
        base, both = conds["baseline"], conds["tags_both"]
        if "embed_ms_per_chunk" not in base:
            continue
        rows.append(
            f"| {dataset} | {short(backend)} | {base['embed_ms_per_chunk']:.1f} "
            f"| {both['embed_ms_per_chunk']:.1f} | {base['db_bytes'] / 1e6:.1f} "
            f"| {both['db_bytes'] / 1e6:.1f} | {base['hybrid'][K]['p50_ms']:.1f} "
            f"| {report.get('query_embed_ms', float('nan')):.1f} |"
        )
    return "\n".join(rows)


def ablation_table(results: dict, mode: str = MODE, k: str = K) -> str:
    """Leave-one-tag-out and suffix-versus-prefix, for runs that have them."""
    rows = [
        "| Dataset | Left out | Pass@20 without | Change versus all tags |",
        "| --- | --- | --- | --- |",
    ]
    placement = []
    for (dataset, backend, _, ablation), report in sorted(results.items()):
        if not ablation or "tags_both" not in report["conditions"]:
            continue
        for condition in report["conditions"]:
            if condition.startswith("without:"):
                d = delta(report, condition, "tags_both", mode, k)
                pass_ = report["conditions"][condition][mode][k]["pass"]
                rows.append(
                    f"| {dataset} ({short(backend)}) | {condition[8:]} "
                    f"| {pass_:.3f} | {fmt(*d)} |"
                )
        if "tags_suffix" in report["conditions"]:
            d = delta(report, "tags_suffix", "tags_embedding", mode, k)
            placement.append(f"| {dataset} ({short(backend)}) | {fmt(*d)} |")
    out = "\n".join(rows)
    if placement:
        out += (
            "\n\n| Dataset | Suffix minus prefix (embedding only) |\n| --- | --- |\n"
            + "\n".join(placement)
        )
    return out


def recovery(results: dict) -> list[dict]:
    """H4: tags' gain over the baseline against the LLM header's gain."""
    out = []
    for (dataset, backend, _sampled, _ablation), report in sorted(results.items()):
        if dataset != "anthropic-llm-context":
            continue
        native = results.get(
            ("anthropic-codebases", backend, False, False)
        ) or results.get(("anthropic-codebases", backend, False, True))
        if native is None:
            continue
        for mode in ("vector", "hybrid"):
            llm = delta(report, "tags_both", "baseline", mode)
            tags = delta(native, "tags_both", "baseline", mode)
            out.append(
                {
                    "embedder": short(backend),
                    "mode": mode,
                    "llm": llm,
                    "tags": tags,
                    "share": tags[0] / llm[0] if llm[0] > 0 else None,
                }
            )
    return out


def rank(values: list[float]) -> list[float]:
    """Average ranks, ties sharing their mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for position in range(i, j + 1):
            ranks[order[position]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    """Spearman rank correlation, or ``None`` when it is undefined."""
    if len(x) < 3:
        return None
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def h5_rows(results: dict, cache: Path, backend: str) -> list[dict]:
    """Each tag's corpus statistics next to its leave-one-tag-out contribution.

    The contribution is Pass@20 with all tags minus Pass@20 without this tag,
    so a positive value means the tag helps.
    """
    rows = []
    for (dataset, used, _, ablation), report in sorted(results.items()):
        if not ablation or used != backend or "tags_both" not in report["conditions"]:
            continue
        if _LOADER.get(dataset) not in LOADERS:
            continue  # a dataset whose loader was retired, such as engineering-brain
        data = LOADERS[_LOADER[dataset]](cache)
        with tempfile.TemporaryDirectory() as directory:
            store = VectorStore(
                settings=Settings(db_path=f"{directory}/s.duckdb", backend=backend)
            )
            store.put_vectors(data.chunks)
            stats = compute_tag_report(
                store,
                report["tag_fields"],
                queries=[query.text for query in data.queries],
            )
            store.close()
        for stat in stats.tags:
            condition = f"without:{stat.label}"
            if condition not in report["conditions"]:
                continue
            diff, low, high, p = delta(report, "tags_both", condition)
            rows.append(
                {
                    "dataset": dataset,
                    "tag": stat.label,
                    "coverage": stat.coverage,
                    "entropy": stat.entropy,
                    "documents_per_value": stat.documents_per_value,
                    "d_lift": stat.d_lift,
                    "value_d_lift": stat.value_d_lift,
                    "query_mention": stat.query_mention,
                    "verdict": stat.verdict,
                    "contribution": diff,
                    "ci": (low, high),
                    "p": p,
                }
            )
    return rows


_LOADER = {
    "anthropic-codebases": "anthropic",
    "ragmate-10k": "ragmate",
    "multihop-rag": "multihop",
    "anthropic-llm-context": "anthropic-context",
}


def _f(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def h5_table(rows: list[dict]) -> str:
    out = [
        "| Dataset | Tag | Coverage | Entropy | Docs per value | Document d lift "
        "| Value d lift | Queries mentioning | Verdict | Contribution to Pass@20 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        out.append(
            f"| {r['dataset']} | {r['tag']} | {r['coverage']:.2f} | {r['entropy']:.2f} "
            f"| {r['documents_per_value']:.1f} | {r['d_lift']:+.2f} "
            f"| {_f(r['value_d_lift'])} | {r['query_mention']:.2f} | {r['verdict']} "
            f"| {fmt(r['contribution'], *r['ci'], r['p'])} |"
        )
    for name in ("d_lift", "value_d_lift", "query_mention"):
        pairs = [(r[name], r["contribution"]) for r in rows if r[name] is not None]
        rho = spearman([a for a, _ in pairs], [b for _, b in pairs])
        out.append(
            f"\nSpearman correlation of {name} with contribution: "
            f"{rho} (n={len(pairs)})"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--directory", type=Path, default=_RESULTS)
    parser.add_argument("--h5", action="store_true", help="compute tag statistics")
    args = parser.parse_args(argv)
    results = load_results(args.directory)
    print("## Gain over the baseline, hybrid Pass@20\n")
    print(gains_table(results))
    print("\n## Cost\n")
    print(cost_table(results))
    print("\n## Ablations\n")
    print(ablation_table(results))
    if args.h5:
        backend = "fastembed:minishlab/potion-multilingual-128M"
        cache = BENCHMARK_CACHE
        print("\n## H5\n")
        print(h5_table(h5_rows(results, cache, backend)))
    print("\n## H4\n")
    print(json.dumps(recovery(results), indent=1, default=lambda x: round(x, 4)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
