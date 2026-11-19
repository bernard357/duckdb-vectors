"""The pre-registered decision (scripts/benchmark_kb_analyze.py)."""


def test_kb_analyze_applies_holm_and_the_ci_to_the_primary_stratum():
    from scripts import benchmark_kb_analyze as kb_analyze

    def report(corpus, p, low, strata=("contextual", "local")):
        entry = {
            "queries": 10,
            "baseline_failure": 0.4,
            "failure_reduction": 0.25,
            "pass": {"difference": 0.1, "ci95": [low, 0.2], "p": p},
        }
        groups = {"all": entry, **{s: entry for s in strata}}
        return {
            "corpus": corpus,
            "backend": "hashing",
            "strata": list(strata),
            "versus_baseline": {"context:both": {"hybrid": {"20": groups}}},
        }

    reports = [
        report("a", 0.001, 0.05),
        report("b", 0.06, 0.01),
        report("c", 0.001, -0.01),
    ]
    assert kb_analyze.decisions(reports) == {
        "a / hashing": True,
        "b / hashing": False,  # p above 0.05 fails Holm
        "c / hashing": False,  # the interval crosses zero
    }
    assert "contextual" in kb_analyze.table(reports)
