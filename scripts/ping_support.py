"""Shared constants and assertions for the end-to-end ping scripts.

The three ping scripts (`ping_docker.py`, `ping_lambda.py`, `ping_agentcore.py`)
build a search container from ``documentation/``, run the same fixed query, and
validate the outcome the same way, so the deployment targets are directly
comparable.
"""

from pathlib import Path

# The repository root, derived from this file's location (scripts/).
REPO_ROOT = Path(__file__).resolve().parent.parent
DOCUMENTATION_DIR = REPO_ROOT / "documentation"

# AWS region and models for the cloud ping scripts.
AWS_REGION = "us-east-1"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
# Nova Pro is invoked through its cross-region inference profile.
RAG_MODEL_ID = "us.amazon.nova-pro-v1:0"

# The fixed query and what a correct retrieval must surface. The query is
# genuinely answered by several documentation pages, and different embedding
# backends rank them differently, so a correct result is retrieving any of the
# pages that actually describe how search works — not one specific filename.
PING_QUERY = "how is a search answered?"
EXPECTED_SOURCES = (
    "duckdb-vectors-primitive.md",
    "duckdb-vectors-guide.md",
    "duckdb-workbook.md",
)
# A grounded answer about search assembly should mention at least one of these.
ANSWER_KEY_TERMS = ("embed", "rank", "fusion", "cosine", "bm25", "retriev")

TOP_K = 5


def assert_sources_contain_expected(sources: list[str]) -> None:
    """Fail unless a page that describes search is among the hit sources."""
    matched = [
        s for s in sources if any(expected in s for expected in EXPECTED_SOURCES)
    ]
    if not matched:
        raise AssertionError(
            f"expected one of {EXPECTED_SOURCES} in retrieved sources, got: {sources}"
        )


def assert_answer_grounded(answer: str) -> None:
    """Fail unless the generated answer is non-empty and mentions a key term."""
    if not answer or not answer.strip():
        raise AssertionError("generated answer is empty")
    lowered = answer.lower()
    if not any(term in lowered for term in ANSWER_KEY_TERMS):
        raise AssertionError(f"answer mentions none of {ANSWER_KEY_TERMS}: {answer!r}")


def report(label: str, sources: list[str], answer: str) -> None:
    """Print a compact, human-readable ping result."""
    print(f"[{label}] query: {PING_QUERY}")
    print(f"[{label}] sources: {sources}")
    print(f"[{label}] answer: {answer.strip()[:400]}")
