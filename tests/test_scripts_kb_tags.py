"""Derived page tags (scripts/kb_tags.py), with a scripted model."""

from development.curation import (
    AnswerCache,
    read_front_matter,
    set_front_matter_key,
)
from development.kb_testing import fake_model
from scripts import (
    kb_tags,
)


def test_kb_tags_discovers_cleans_tags_twice_and_applies(kb, tmp_path):
    model = fake_model()
    schema = kb_tags.discover(kb, model, ["refund for Cedar"], size=4)
    product = schema.fields[0]
    assert product.values == ["Beacon", "Cedar"]
    assert schema.removed_values == {"product": ["Other"]}
    cache = AnswerCache(tmp_path / "cache", model.usage)
    tags, agreement = kb_tags.tag_folder(kb, schema, model, cache, workers=2)
    assert agreement["product"] == 1.0
    assert agreement["mood"] == 0.0  # the two passes disagree on every page
    assert all(values["mood"] is None for values in tags.values())
    changed = kb_tags.apply_tags(kb, tags, ["product"])
    assert changed == sum(1 for values in tags.values() if values["product"])
    front = read_front_matter((kb / "deploy-runbook.md").read_text("utf-8"))
    assert front["retrieval"] == {"product": "Cedar"}
    assert len(kb_tags.review_rows(tags, ["product"])) == len(tags)


def test_kb_tags_existing_fields_skip_reserved_keys(kb):
    fields = kb_tags.existing_fields(kb_tags.pages(kb))
    assert "title" in fields and "retrieval" not in fields and "sections" not in fields


def test_kb_tags_clean_value_rejects_sentinels_and_long_free_text():
    field = kb_tags.Field(name="x", description="", values="free")
    assert kb_tags.clean_value(field, "Other") is None
    assert kb_tags.clean_value(field, "a b c d e") is None
    assert kb_tags.clean_value(field, "Cedar") == "Cedar"


def test_kb_tags_apply_moves_legacy_fields_and_keeps_the_rest(kb, caplog):
    page = kb / "deploy-runbook.md"
    text = page.read_text(encoding="utf-8")
    text = set_front_matter_key(text, "llm", {"product": "Beacon", "mine": "x"})
    page.write_text(text, encoding="utf-8")
    assert "llm" not in kb_tags.existing_fields(kb_tags.pages(kb))
    assert "legacy 'llm' key" in caplog.text
    kb_tags.apply_tags(kb, {"deploy-runbook.md": {"product": "Cedar"}}, ["product"])
    front = read_front_matter(page.read_text(encoding="utf-8"))
    assert front["retrieval"] == {"product": "Cedar"}
    assert front["llm"] == {"mine": "x"}
    kb_tags.apply_tags(kb, {"deploy-runbook.md": {"mine": "y"}}, ["mine"])
    front = read_front_matter(page.read_text(encoding="utf-8"))
    assert "llm" not in front
    assert front["retrieval"] == {"product": "Cedar", "mine": "y"}
