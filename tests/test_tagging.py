"""Tests for tag projection, the embedded-text builder and tagged indexing."""

import shutil

import duckdb
import pytest

from duckdb_vectors.config import Settings
from duckdb_vectors.etag import compute_etag
from duckdb_vectors.indexer import index_folder
from duckdb_vectors.store import VectorStore
from duckdb_vectors.tagging import (
    TagField,
    parse_tag_field,
    parse_tag_fields,
    project_many,
    project_tags,
    tagged_text,
)

PRODUCT_TAGS = ["product: $.product.name"]

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


# Indexing with tags


def _store(tmp_path, name="idx.duckdb", **settings):
    return VectorStore(
        settings=Settings(db_path=str(tmp_path / name), backend="hashing", **settings)
    )


def test_corpus_fixture_has_every_tag_pattern(tagged_store):
    chunks = tagged_store.list_vectors()
    assert len(chunks) == 16
    metadata = [chunk.metadata for chunk in chunks]
    assert all(m["kind"] == "guide" for m in metadata)
    assert len({m["doc_id"] for m in metadata}) == 8
    assert sum("owner" in m for m in metadata) == 4
    assert {m["published"] for m in metadata} >= {"2026-03-01"}


def test_stored_text_stays_raw_and_the_embedding_sees_the_tags(tagged_store):
    chunk = tagged_store.list_vectors()[0]
    assert "product:" not in chunk.text
    tagged = tagged_store.embedded_texts([chunk])[0]
    assert tagged.startswith("product: ")
    assert tagged.endswith(f"\n\n{chunk.text}")
    stored = tagged_store._con.execute(
        "SELECT embedding, etag FROM chunks WHERE chunk_id = ?", [chunk.chunk_id]
    ).fetchone()
    assert list(stored[0]) == pytest.approx(tagged_store.embedder.embed([tagged])[0])
    assert stored[1] == compute_etag(
        tagged,
        tagged_store.embedder.model_identifier,
        tagged_store.embedder.dimensions,
    )


def test_index_is_identical_to_untagged_when_tagging_is_off(tmp_path, tagged_folder):
    store = _store(tmp_path)
    index_folder(store, tagged_folder)
    for chunk in store.list_vectors():
        row = store._con.execute(
            "SELECT embedding, etag FROM chunks WHERE chunk_id = ?", [chunk.chunk_id]
        ).fetchone()
        assert row[1] == compute_etag(
            chunk.text, store.embedder.model_identifier, store.embedder.dimensions
        )
        assert list(row[0]) == pytest.approx(store.embedder.embed([chunk.text])[0])
    store.close()


def test_changing_the_tags_re_embeds_only_chunks_that_gain_a_prefix(
    tmp_path, tagged_folder
):
    store = _store(tmp_path)
    first = index_folder(store, tagged_folder)
    assert first.embedded == 16
    store.close()
    # "owner" exists on 2 of 8 documents (4 of 16 chunks): only they change.
    store = _store(tmp_path, tag_fields=["owner: $.owner"])
    second = index_folder(store, tagged_folder)
    assert second.embedded == 4
    assert second.reused == 12
    store.close()


def test_changing_one_frontmatter_value_re_embeds_only_its_chunks(
    tmp_path, tagged_folder
):
    corpus = tmp_path / "corpus"
    shutil.copytree(tagged_folder, corpus)
    store = _store(tmp_path, tag_fields=PRODUCT_TAGS)
    assert index_folder(store, corpus).embedded == 16
    assert index_folder(store, corpus).embedded == 0
    page = corpus / "pricing.md"
    page.write_text(page.read_text().replace("name: Atlas", "name: Borealis"))
    report = index_folder(store, corpus)
    assert report.embedded == 2
    assert report.written == 2
    store.close()


def test_put_vectors_embeds_the_tagged_text_too(tmp_path):
    store = _store(tmp_path, tag_fields=["kind: $.kind"])
    store.add_texts(ids=["a"], texts=["alpha beta"], metadatas=[{"kind": "faq"}])
    row = store._con.execute("SELECT text, etag FROM chunks").fetchone()
    assert row[0] == "alpha beta"
    assert row[1] == compute_etag(
        "kind: faq\n\nalpha beta",
        store.embedder.model_identifier,
        store.embedder.dimensions,
    )
    store.close()
