"""Tables and the pre-registered decision over ``kb_evaluate`` result files.

The primary comparison is ``context:both`` against the baseline, hybrid,
Pass@20, in the primary stratum of each corpus (``contextual`` where it exists,
``all`` otherwise). It "helps" when the 95% CI excludes zero and the sign-flip
p-value survives a Holm correction over every primary test given.

    $ .venv/bin/python -m scripts.benchmark_kb_analyze \\
        tmp/kb/*/results/test-*.json
"""

import argparse
import json
from pathlib import Path

from development.metrics import holm

PRIMARY = "context:both"
MODE = "hybrid"
K = "20"
SHORT = {
    "fastembed:minishlab/potion-multilingual-128M": "potion-256",
    "fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": (
        "MiniLM-384"
    ),
    "fastembed:BAAI/bge-small-en-v1.5": "bge-small-384",
}


def primary_stratum(report: dict) -> str:
    return "contextual" if "contextual" in report["strata"] else "all"


def cell(entry: dict, metric: str = "pass") -> str:
    test = entry[metric]
    low, high = test["ci95"]
    star = " *" if low > 0 or high < 0 else ""
    return f"{test['difference']:+.3f} [{low:+.3f}, {high:+.3f}]{star}"


def decisions(reports: list[dict]) -> dict[str, bool]:
    pvalues = {}
    for report in reports:
        stratum = primary_stratum(report)
        entry = report["versus_baseline"][PRIMARY][MODE][K][stratum]
        pvalues[key(report)] = entry["pass"]["p"]
    rejected = holm(pvalues)
    out = {}
    for report in reports:
        stratum = primary_stratum(report)
        entry = report["versus_baseline"][PRIMARY][MODE][K][stratum]
        low, high = entry["pass"]["ci95"]
        out[key(report)] = rejected[key(report)] and (low > 0 or high < 0)
    return out


def key(report: dict) -> str:
    return f"{report['corpus']} / {SHORT.get(report['backend'], report['backend'])}"


def table(reports: list[dict], strata: str | None = None) -> str:
    """One row per corpus, embedder and stratum: baseline and gains at k=20."""
    conditions = [
        c for c in reports[0]["versus_baseline"] if c.endswith((":both", ":embedding"))
    ]
    header = ["Corpus / embedder", "Stratum", "Queries", "Baseline failure@20"]
    header += [f"{c} Pass@20" for c in conditions] + ["Failure reduction, context:both"]
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    for report in reports:
        groups = report["versus_baseline"][conditions[0]][MODE][K]
        for stratum, first in groups.items():
            if strata and stratum not in strata:
                continue
            if len(groups) == 2 and stratum != "all":
                continue  # one stratum only: its row repeats "all"
            row = [key(report), stratum, str(first["queries"])]
            row.append(f"{first['baseline_failure']:.3f}")
            for condition in conditions:
                row.append(cell(report["versus_baseline"][condition][MODE][K][stratum]))
            both = report["versus_baseline"].get(PRIMARY, {}).get(MODE, {})
            reduction = both.get(K, {}).get(stratum, {}).get("failure_reduction")
            row.append("" if reduction is None else f"{reduction:.0%}")
            lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args(argv)
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.results]
    reports.sort(key=key)
    corpora: dict[str, list[dict]] = {}
    for report in reports:  # each corpus has its own tag sets, so its own table
        corpora.setdefault(report["corpus"], []).append(report)
    for group in corpora.values():
        print(table(group))
        print()
    for name, helps in decisions(reports).items():
        print(f"- {name}: {'helps' if helps else 'not shown'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
