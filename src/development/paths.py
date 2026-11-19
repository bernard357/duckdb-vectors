"""Where the repository keeps its inputs and its local, untracked outputs."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures"
PROMPTS = FIXTURES / "prompts"
# Downloaded public datasets and benchmark results: local, never committed.
BENCHMARK_CACHE = REPO_ROOT / "tmp" / "benchmarks" / "cache"
BENCHMARK_RESULTS = REPO_ROOT / "tmp" / "benchmarks" / "results"
