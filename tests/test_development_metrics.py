"""Retrieval metrics and tests (src/development/metrics.py)."""

import pytest

from development import metrics


def test_metrics_ndcg_sign_flip_and_holm():
    assert metrics.ndcg(["a", "b"], {"a": 2, "b": 1}, 2) == 1.0
    assert metrics.ndcg(["x", "a"], {"a": 2}, 2) == pytest.approx(1 / 1.58496, 1e-4)
    assert metrics.sign_flip_p([0.0] * 30, [1.0] * 30) < 0.001
    assert metrics.sign_flip_p([0.5] * 10, [0.5] * 10) == 1.0
    assert metrics.holm({"a": 0.01, "b": 0.04, "c": 0.03}) == {
        "a": True,
        "b": False,
        "c": False,
    }
