"""Validate a ping result read as JSON on stdin.

The shell ping snippets (fixtures/shell/ping-*.sh) pipe the agent's
``{"answer": ..., "sources": [...]}`` output to this validator, which owns the
assertions so the shell does not have to parse JSON or reason about relevance.

Usage::

    echo '{"answer": "...", "sources": [...]}' | python scripts/ping_validate.py docker
"""

import json
import sys

from ping_support import (
    assert_answer_grounded,
    assert_sources_contain_expected,
    report,
)


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "ping"
    raw = sys.stdin.read()
    # Tolerate surrounding noise (CLI banners, soft-wrap newlines) by slicing
    # to the outermost JSON object and collapsing whitespace inside it.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        print(f"[{label}] FAIL: no JSON object in input:\n{raw}")
        return 1
    payload = " ".join(raw[start : end + 1].split())
    result = json.loads(payload)
    sources = result.get("sources", [])
    answer = result.get("answer", "")
    report(label, sources, answer)
    assert_sources_contain_expected(sources)
    assert_answer_grounded(answer)
    print(f"[{label}] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
