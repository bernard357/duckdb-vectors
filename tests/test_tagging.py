"""Tests for tag projection and the embedded-text builder."""

import pytest

from duckdb_vectors.tagging import (
    TagField,
    parse_tag_field,
    parse_tag_fields,
    project_many,
    project_tags,
    tagged_text,
)

METADATA = {
    "title": "Refund policy",
    "product": {"name": "Atlas", "tier": ["enterprise", "consumer"]},
    "audience": ["finance", "support"],
    "authors": [{"name": "ada"}, {"name": "lin"}],
    "published": "2026-03-01",
    "my-key": "hyphen",
    "empty": [],
    "nothing": None,
}


def fields(*specs: str) -> list[TagField]:
    return parse_tag_fields(list(specs))


def test_parse_tag_field_splits_on_first_colon():
    parsed = parse_tag_field("product: $.product.name")
    assert parsed == TagField(label="product", path="$.product.name")


@pytest.mark.parametrize(
    "spec", ["$.title", "title:", ": $.title", "title: title", "title $.title"]
)
def test_parse_tag_field_rejects_malformed_specs(spec):
    with pytest.raises(ValueError, match="expected 'label: \\$.path'"):
        parse_tag_field(spec)


def test_parse_tag_fields_accepts_semicolon_string_and_none():
    parsed = parse_tag_fields("a: $.a; b: $.b ;; ")
    assert [f.label for f in parsed] == ["a", "b"]
    assert parse_tag_fields(None) == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("$.title", "Refund policy"),
        ("$.product.name", "Atlas"),
        ("$.product.tier[*]", "enterprise, consumer"),
        ("$.audience[*]", "finance, support"),
        ("$.audience[0]", "finance"),
        ("$.audience[#-1]", "support"),
        ("$.authors[*].name", "ada, lin"),
        ("$.published", "2026-03-01"),
        ('$."my-key"', "hyphen"),
    ],
)
def test_projection_evaluates_json_path(path, expected):
    assert project_tags(METADATA, fields(f"tag: {path}")) == [("tag", expected)]


@pytest.mark.parametrize(
    "path", ["$.missing", "$.missing.deep", "$.empty[*]", "$.nothing", "$.empty[0]"]
)
def test_missing_or_empty_values_give_no_tag(path):
    assert project_tags(METADATA, fields(f"tag: {path}")) == []


def test_tags_keep_the_declared_order():
    spec = fields("b: $.product.name", "a: $.title", "c: $.audience[*]")
    assert [label for label, _ in project_tags(METADATA, spec)] == ["b", "a", "c"]


def test_filter_expressions_are_rejected_by_duckdb():
    import duckdb

    with pytest.raises(duckdb.Error):
        project_tags(METADATA, fields("x: $.authors[?(@.name=='ada')].name"))


def test_project_many_keeps_input_order_and_deduplicates():
    metadatas = [{"k": "one"}, {"k": "two"}, {"k": "one"}, {}]
    result = project_many(metadatas, fields("k: $.k"))
    assert result == [[("k", "one")], [("k", "two")], [("k", "one")], []]


def test_no_fields_or_no_metadata():
    assert project_many([{"a": 1}], []) == [[]]
    assert project_many([], fields("a: $.a")) == []


def test_tagged_text_prefixes_tags_before_the_raw_text():
    text = tagged_text("The window is 30 days.", [("title", "Refunds"), ("a", "b, c")])
    assert text == "title: Refunds\na: b, c\n\nThe window is 30 days."


def test_tagged_text_without_tags_is_the_raw_text():
    assert tagged_text("raw", []) == "raw"


def test_prefix_is_never_truncated():
    long_value = "word " * 5000
    text = tagged_text("body", [("note", long_value)])
    assert text.startswith(f"note: {long_value}")
    assert text.endswith("\n\nbody")
