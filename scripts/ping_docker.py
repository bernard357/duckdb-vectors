"""End-to-end ping: build the local RAG container and validate a search.

Builds the offline RAG agent image from ``documentation/``, runs the fixed
query in a container, and asserts that the expected documentation page is
retrieved and that the locally generated answer is grounded. No AWS, no
network at run time.

Usage::

    $ python scripts/ping_docker.py
"""

import json
import subprocess
import sys

from ping_support import (
    PING_QUERY,
    REPO_ROOT,
    assert_answer_grounded,
    assert_sources_contain_expected,
    report,
)

IMAGE_TAG = "duckdb-vectors-rag-ping"
DOCKERFILE = "examples/docker-rag-agent/Dockerfile"


def build_image() -> None:
    print(f"[docker] building {IMAGE_TAG} (this bakes the model; be patient)...")
    subprocess.run(
        ["docker", "build", "-f", DOCKERFILE, "-t", IMAGE_TAG, "."],
        cwd=REPO_ROOT,
        check=True,
    )


def run_query() -> dict:
    print("[docker] running the query in a container...")
    completed = subprocess.run(
        ["docker", "run", "--rm", IMAGE_TAG, PING_QUERY],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    # The agent prints a single JSON object on the last non-empty stdout line.
    lines = [ln for ln in completed.stdout.splitlines() if ln.strip()]
    if not lines:
        raise AssertionError(f"no output from container; stderr: {completed.stderr}")
    return json.loads(lines[-1])


def main() -> int:
    build_image()
    result = run_query()
    sources = result.get("sources", [])
    answer = result.get("answer", "")
    report("docker", sources, answer)
    assert_sources_contain_expected(sources)
    assert_answer_grounded(answer)
    print("[docker] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
