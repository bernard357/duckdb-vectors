"""Enrichment of a Markdown folder with model-derived tags, with a fake model."""

import json

import pytest

from benchmarks.tagged_retrieval import enrich
from benchmarks.tagged_retrieval.enrich import (
    Field,
    Schema,
    apply_block,
    apply_tags,
    clean_value,
    discover,
    parse_json_answer,
    tag_folder,
)

SCHEMA = Schema(
    prompt_version=1,
    model="fake",
    fields=[
        Field(name="kind", description="Type", values=["guide", "runbook"]),
        Field(name="subject", description="Main subject", values="free"),
    ],
)

PAGE = '---\ntopic: "podman"\n---\n# Podman\n\nBody text.\n'


def test_parse_json_answer_tolerates_fences_and_rejects_prose():
    assert parse_json_answer('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError, match="no JSON object"):
        parse_json_answer("sorry, I cannot")


def test_clean_value_keeps_only_values_the_schema_allows():
    kind, subject = SCHEMA.fields
    assert clean_value(kind, "guide") == "guide"
    assert clean_value(kind, "tutorial") is None
    assert clean_value(kind, None) is None
    assert clean_value(subject, " container engine ") == "container engine"
    assert clean_value(subject, "a very long and rambling subject phrase") is None
    assert clean_value(subject, "  ") is None


def test_sentinel_answers_count_as_no_value():
    subject = SCHEMA.fields[1]
    for answer in ("Other", "none", "N/A", "Unknown"):
        assert clean_value(subject, answer) is None


def test_apply_block_adds_the_namespace_and_touches_nothing_else():
    updated = apply_block(PAGE, {"kind": "guide", "subject": None})
    assert updated == (
        '---\ntopic: "podman"\nllm:\n  kind: "guide"\n---\n# Podman\n\nBody text.\n'
    )


def test_apply_block_is_idempotent_and_replaces_a_previous_block():
    first = apply_block(PAGE, {"kind": "guide", "subject": "podman"})
    assert apply_block(first, {"kind": "guide", "subject": "podman"}) == first
    second = apply_block(first, {"kind": "runbook", "subject": None})
    assert second.count("llm:") == 1
    assert 'kind: "runbook"' in second
    assert 'podman"\n  subject' not in second
    assert second.endswith("---\n# Podman\n\nBody text.\n")


def test_apply_block_with_no_values_removes_the_block():
    first = apply_block(PAGE, {"kind": "guide"})
    assert apply_block(first, {"kind": None}) == PAGE


def test_apply_block_requires_front_matter():
    with pytest.raises(ValueError, match="no front matter"):
        apply_block("# Title\n", {"kind": "guide"})


def test_discover_samples_pages_and_returns_the_schema(tmp_path):
    for n in range(3):
        (tmp_path / f"p{n}.md").write_text(PAGE)
    seen = {}

    def invoke(system, user):
        seen["user"] = user
        return json.dumps(
            {
                "fields": [{"name": "kind", "description": "d", "values": ["a", "b"]}],
                "rejected": [{"name": "title", "reason": "unique"}],
            }
        )

    schema = discover(tmp_path, invoke, "fake")
    assert [f.name for f in schema.fields] == ["kind"]
    assert schema.rejected[0]["name"] == "title"
    assert "### p0.md" in seen["user"]
    assert "topic" in seen["user"]  # the existing field is not proposed again
    assert "title: " not in seen["user"]  # front matter is not shown to the model


def test_tagging_is_cached_by_content_and_schema(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.md").write_text(PAGE)
    calls = []

    def invoke(system, user):
        calls.append(user)
        return '{"kind": "guide", "subject": "podman", "extra": "ignored"}'

    cache = tmp_path / "cache"
    first = tag_folder(folder, SCHEMA, invoke, cache)
    assert first == {"a.md": {"kind": "guide", "subject": "podman"}}
    assert tag_folder(folder, SCHEMA, invoke, cache) == first
    assert len(calls) == 1
    (folder / "a.md").write_text(PAGE + "More.\n")
    tag_folder(folder, SCHEMA, invoke, cache)
    assert len(calls) == 2  # a changed page costs one call
    assert "Allowed: guide, runbook" in calls[0]
    assert "Body text." in calls[0] and "topic" not in calls[0]


def test_apply_tags_reports_how_many_pages_changed(tmp_path):
    (tmp_path / "a.md").write_text(PAGE)
    (tmp_path / "b.md").write_text(PAGE)
    tags = {"a.md": {"kind": "guide"}, "b.md": {"kind": None}}
    assert apply_tags(tmp_path, tags) == 1
    assert apply_tags(tmp_path, tags) == 0
    assert (tmp_path / "b.md").read_text() == PAGE


def test_coverage_report_counts_values():
    tags = {
        "a.md": {"kind": "guide"},
        "b.md": {"kind": "guide"},
        "c.md": {"kind": None},
    }
    report = enrich.coverage_report(SCHEMA, tags)
    assert report["kind"]["tagged"] == 2
    assert report["kind"]["top"] == [("guide", 2)]
