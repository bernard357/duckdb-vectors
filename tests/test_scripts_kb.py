"""The curation scripts together (scripts/kb_*.py), offline with ``--model fake``."""

import json

from development.curation import (
    read_front_matter,
)
from development.kb_testing import HASHING
from scripts import (
    kb_context,
    kb_evaluate,
    kb_tags,
    kb_validation,
)


def test_curation_chain_runs_offline_with_the_fake_model(kb, tmp_path):
    """What ``make kb-validation``, ``kb-context``, ``kb-tags`` and
    ``kb-evaluate`` run, with ``--model fake``: no AWS, no cost."""
    from scripts import benchmark_kb_analyze as kb_analyze

    work = str(tmp_path / "work")
    fake = ["--model", "fake"]
    common = [str(kb), "--work", work]
    steps = [
        (kb_validation, ["local", *common, *fake, "--size", "20", "--min-words", "10"]),
        (
            kb_validation,
            ["contextual", *common, *fake, "--size", "10", "--min-words", "10"]
            + ["--backends", HASHING],
        ),
        (kb_validation, ["split", *common]),
        (kb_validation, ["pool", *common, "--backends", HASHING]),
        (kb_validation, ["judge", *common, *fake]),
        (kb_validation, ["audit", *common, "--size", "6"]),
        (kb_context, [*common, *fake]),
        (kb_tags, ["discover", *common, *fake]),
        (kb_tags, ["tag", *common, *fake]),
        (kb_tags, ["apply", *common]),
    ]
    for module, argv in steps:
        assert module.main(argv) == 0, argv[0]
    validation = json.loads((tmp_path / "work" / "validation.json").read_text())
    strata = {item["stratum"] for item in validation["items"]}
    assert strata == {"local", "contextual"}
    assert all(item["golden"] for item in validation["items"] if item["valid"])
    assert (tmp_path / "work" / "audit.yaml").exists()
    front = read_front_matter((kb / "refund-policy.md").read_text())
    assert front["retrieval"]["subject"] and front["sections"]
    output = tmp_path / "work" / "results" / "all.json"
    argv = [*common, "--split", "all", "--backend", HASHING, "--output", str(output)]
    argv += ["--tags", "context=context: $.retrieval.context"]
    assert kb_evaluate.main(argv) == 0
    assert kb_analyze.main([str(output)]) == 0
