"""Validation sets (scripts/kb_validation.py), with a scripted model."""

import pytest

from development import curation
from development.kb_testing import HASHING
from development.kb_testing import small_validation_set as _validation
from scripts import (
    kb_validation,
)


def test_validation_local_drops_copies_and_records_origin(kb, tmp_path):
    validation, chunks, model, cache = _validation(kb, tmp_path)
    report = kb_validation.generate_local(validation, chunks, model, cache, size=4)
    assert report["copied_too_much"] == 4
    assert report["kept"] == len(validation.items) == 4
    for item in validation.items:
        assert item.stratum == "local" and len(item.origin) == 1


def test_validation_contextual_pairs_come_from_different_pages(kb, tmp_path):
    validation, chunks, model, cache = _validation(kb, tmp_path)
    kb_validation.generate_contextual(
        validation, kb, chunks, model, cache, size=3, backend=HASHING
    )
    by_id = {c.chunk_id: c for c in chunks}
    assert validation.items
    for item in validation.items:
        assert item.stratum == "contextual"
        assert by_id[item.confuser].source != item.source


def test_validation_split_is_by_page_and_frozen(kb, tmp_path):
    validation, chunks, model, cache = _validation(kb, tmp_path)
    kb_validation.generate_local(validation, chunks, model, cache, size=8)
    split = kb_validation.freeze_split(validation, dev_share=0.5)
    by_page: dict[str, set] = {}
    for item in validation.items:
        by_page.setdefault(item.source, set()).add(item.split)
    assert all(len(splits) == 1 for splits in by_page.values())
    assert split["dev"] + split["test"] == len(validation.items)
    with pytest.raises(ValueError, match="frozen"):
        kb_validation.freeze_split(validation, dev_share=0.3)
    assert kb_validation.freeze_split(validation, dev_share=0.5) == split


def test_validation_pool_judge_audit_and_kappa(kb, tmp_path):
    validation, chunks, model, cache = _validation(kb, tmp_path)
    kb_validation.generate_local(validation, chunks, model, cache, size=4)
    kb_validation.import_queries(validation, ["refund window", "refund window", ""])
    assert sum(item.stratum == "real" for item in validation.items) == 1
    kb_validation.pool(validation, chunks, [HASHING], ["product: $.product.name"], 5)
    for item in validation.items:
        assert set(item.origin) <= set(item.pooled)
    report = kb_validation.judge(validation, chunks, model, cache, workers=2)
    assert report["judged"] == sum(len(item.pooled) for item in validation.items)
    for item in validation.items:
        assert item.golden == [c for c in item.pooled if item.grades[c] >= 1]
    rows = kb_validation.audit_rows(validation, chunks, size=4)
    assert rows and all(row["human_grade"] == "" for row in rows)
    sheet = tmp_path / "audit.yaml"
    curation.write_sheet(
        sheet,
        kb_validation.audit_sheet(rows),
        kb_validation.AUDIT_HEADER,
        kb_validation.AUDIT_HINTS,
    )
    text = sheet.read_text(encoding="utf-8")
    assert "judge_grade" not in text and "human_grade:  # 0, 1 or 2" in text
    # The person fills the blanks in place; a "no" still counts as rated.
    grades = {(row["item"], row["chunk"]): row["judge_grade"] for row in rows}
    records = curation.read_sheet(sheet)
    for number, record in enumerate(records):
        record["question_valid"] = number > 0  # YAML reads yes / no as booleans
        for passage in record["passages"]:
            passage["human_grade"] = grades[(record["item"], passage["chunk"])]
    graded = kb_validation.sheet_rows(records)
    for row in graded:
        row["judge_grade"] = grades[(row["item"], row["chunk"])]
    result = kb_validation.agreement(graded)
    assert result["rows"] == len(rows) and result["kappa_binary"] == 1.0
    assert result["questions_rated"] == len(records)
    assert result["questions_valid"] == round(1 - 1 / len(records), 3)


def test_cohen_kappa():
    assert kb_validation.cohen_kappa([0, 1, 2, 2], [0, 1, 2, 2]) == 1.0
    assert kb_validation.cohen_kappa([0, 0, 1, 1], [0, 1, 0, 1]) == 0.0
