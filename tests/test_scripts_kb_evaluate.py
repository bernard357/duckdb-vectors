"""Tag set evaluation (scripts/kb_evaluate.py)."""

import pytest

from development.curation import (
    AnswerCache,
    chunk_folder,
)
from development.kb_testing import HASHING, fake_model
from scripts import (
    kb_context,
    kb_evaluate,
    kb_validation,
)


def test_kb_evaluate_runs_every_condition_per_stratum(kb, tmp_path):
    model = fake_model()
    kb_context.apply_contexts(
        kb,
        kb_context.contextualize_folder(
            kb, model, AnswerCache(tmp_path / "c", model.usage), workers=2
        ),
    )
    chunks = chunk_folder(kb)
    items = [
        kb_validation.Item(
            id=f"q{i}",
            text=chunk.text[:60],
            stratum="local" if i % 2 else "contextual",
            golden=[chunk.chunk_id],
            grades={chunk.chunk_id: 2},
            split="test",
        )
        for i, chunk in enumerate(chunks[:6])
    ]
    report = kb_evaluate.run_evaluation(
        chunks,
        items,
        HASHING,
        {"context": ["context: $.retrieval.context"]},
        ks=(5, 10),
    )
    assert list(report["conditions"]) == [
        "baseline",
        "context:embedding",
        "context:bm25",
        "context:both",
    ]
    hybrid = report["versus_baseline"]["context:both"]["hybrid"]["10"]
    assert set(hybrid) == {"all", "contextual", "local"}
    assert hybrid["all"]["queries"] == 6
    assert "failure_reduction" in hybrid["all"]
    # Tags in the keyword index only leave the vectors as in the baseline.
    base = report["conditions"]["baseline"]["vector"]["k"]["10"]["scores"]
    bm25 = report["conditions"]["context:bm25"]["vector"]["k"]["10"]["scores"]
    assert base == bm25
    assert "failure" in kb_evaluate.summary(report, "10")


def test_kb_evaluate_parse_tag_set():
    assert kb_evaluate.parse_tag_set("ctx=context: $.retrieval.context;p: $.p") == (
        "ctx",
        ["context: $.retrieval.context", "p: $.p"],
    )
    with pytest.raises(ValueError):
        kb_evaluate.parse_tag_set("no-equals")
