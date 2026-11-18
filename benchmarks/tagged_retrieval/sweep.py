"""Run the benchmark matrix: datasets by embedders, resumable.

Each job writes one JSON file in ``results/``; a job whose file already exists
is skipped, so an interrupted sweep continues where it stopped.

The large embedders (``e5-large``, ``bge-large``) run on the Anthropic codebase
set only: on a laptop CPU they embed under one chunk per second, which is hours
for the larger corpora. The static default and the two small transformers run
everywhere, and the leave-one-tag-out and suffix ablations run with the default.

    $ ENGINEERING_BRAIN_DIR=.../documentation \\
      .venv/bin/python -m benchmarks.tagged_retrieval.sweep
"""

import argparse
import sys
import time

from benchmarks.tagged_retrieval import run

POTION = "fastembed:minishlab/potion-multilingual-128M"
MINILM = "fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
BGE_SMALL = "fastembed:BAAI/bge-small-en-v1.5"
E5_LARGE = "fastembed:intfloat/multilingual-e5-large"
BGE_LARGE = "fastembed:BAAI/bge-large-en-v1.5"
SAMPLE = 500
# Datasets whose queries are sampled to bound run time.
SAMPLED = {"multihop"}
ABLATED = {"anthropic", "ragmate", "multihop"}


def jobs() -> list[tuple[str, str, bool]]:
    """``(dataset, backend, ablate)`` in the order they should run."""
    datasets = [
        "anthropic",
        "anthropic-context",
        "ragmate",
        "multihop",
    ]
    plan = [(d, POTION, d in ABLATED) for d in datasets]
    for backend in (MINILM, BGE_SMALL):
        plan += [(d, backend, False) for d in datasets]
    plan += [("anthropic", E5_LARGE, False), ("anthropic", BGE_LARGE, False)]
    return plan


def arguments(dataset: str, backend: str, ablate: bool) -> list[str]:
    args = ["--dataset", dataset, "--backend", backend]
    if dataset in SAMPLED:
        args += ["--sample-queries", str(SAMPLE)]
    if ablate:
        args += ["--ablate", "--suffix"]
    return args


def done(dataset: str, backend: str, ablate: bool) -> bool:
    """Whether a result file for this job exists, whatever commit made it."""
    name = backend.replace(":", "_").replace("/", "_")
    sampled = f"-q{SAMPLE}" if dataset in SAMPLED else ""
    ablation = "-ablation" if ablate else ""
    pattern = f"*-{dataset}-{name}{sampled}{ablation}.json"
    return any(run._RESULTS.glob(pattern))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only-backend", default=None)
    args = parser.parse_args(argv)
    for dataset, backend, ablate in jobs():
        if args.only_backend and args.only_backend not in backend:
            continue
        label = f"{dataset} / {backend.split(':')[1]}"
        if done(dataset, backend, ablate):
            print(f"skip   {label}", flush=True)
            continue
        started = time.time()
        print(f"start  {label}", flush=True)
        run.main(arguments(dataset, backend, ablate))
        print(f"done   {label} in {(time.time() - started) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
