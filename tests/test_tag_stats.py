"""Tag statistics on the tagged fixture corpus (8 documents, 16 chunks)."""

import json
from collections import Counter

import pytest
from typer.testing import CliRunner

from duckdb_vectors import tag_stats
from duckdb_vectors.cli import app
from duckdb_vectors.tag_stats import _entropy, compute_tag_report, input_window

ALL_TAGS = [
    "product: $.product.name",
    "audience: $.audience[*]",
    "kind: $.kind",
    "doc_id: $.doc_id",
    "owner: $.owner",
    "published: $.published",
]

pytestmark = pytest.mark.slow

runner = CliRunner()


@pytest.fixture
def report(tagged_store):
    return compute_tag_report(tagged_store, ALL_TAGS)


def stat(report, label):
    return next(tag for tag in report.tags if tag.label == label)


def test_report_counts_documents_and_chunks(report):
    assert report.documents == 8
    assert report.chunks == 16
    assert [tag.label for tag in report.tags] == [
        "product",
        "audience",
        "kind",
        "doc_id",
        "owner",
        "published",
    ]


def test_coverage_cardinality_and_documents_per_value(report):
    product = stat(report, "product")
    assert (product.documents, product.coverage, product.cardinality) == (8, 1.0, 3)
    assert product.documents_per_value == pytest.approx(8 / 3)
    owner = stat(report, "owner")
    assert (owner.documents, owner.coverage, owner.cardinality) == (2, 0.25, 1)
    assert stat(report, "kind").cardinality == 1
    assert stat(report, "doc_id").documents_per_value == 1.0


def test_entropy_runs_from_constant_to_uniform(report):
    assert stat(report, "kind").entropy == 0.0
    assert stat(report, "doc_id").entropy == pytest.approx(1.0)
    assert 0.9 < stat(report, "product").entropy < 1.0
    assert _entropy(Counter({"a": 1, "b": 1})) == pytest.approx(1.0)
    assert _entropy(Counter({"a": 5})) == 0.0
    assert _entropy(Counter()) == 0.0


@pytest.mark.parametrize(
    ("label", "verdict", "reason"),
    [
        ("product", "effective", None),
        ("audience", "effective", None),
        ("kind", "decorative", "constant"),
        ("doc_id", "decorative", "identifier"),
        ("owner", "decorative", "sparse"),
        ("published", "decorative", "identifier"),
    ],
)
def test_each_pattern_gets_its_verdict(report, label, verdict, reason):
    tag = stat(report, label)
    assert tag.verdict == verdict
    if reason is None:
        assert tag.reasons == []
    else:
        assert any(item.startswith(reason) for item in tag.reasons)


def test_a_useful_tag_widens_the_same_document_margin(report):
    product = stat(report, "product")
    assert product.margin_with > product.margin_without
    assert product.margin_lift == pytest.approx(
        product.margin_with - product.margin_without
    )
    assert product.d_lift > tag_stats.MIN_LIFT_D
    assert stat(report, "kind").d_lift < tag_stats.MIN_LIFT_D


def test_report_is_deterministic(tagged_store):
    first = compute_tag_report(tagged_store, ALL_TAGS)
    second = compute_tag_report(tagged_store, ALL_TAGS)
    assert first == second


def test_tags_default_to_the_store_settings(tagged_store):
    report = compute_tag_report(tagged_store)
    assert [tag.label for tag in report.tags] == ["product", "audience"]


def test_no_tags_declared_is_an_error(tmp_path, tagged_folder):
    from duckdb_vectors.config import Settings
    from duckdb_vectors.indexer import index_folder
    from duckdb_vectors.store import VectorStore

    store = VectorStore(
        settings=Settings(db_path=str(tmp_path / "x.duckdb"), backend="hashing")
    )
    index_folder(store, tagged_folder)
    with pytest.raises(ValueError, match="No tags declared"):
        compute_tag_report(store)
    store.close()


@pytest.mark.parametrize(
    ("backend", "window"),
    [
        ("fastembed:minishlab/potion-multilingual-128M", 512),
        ("fastembed", 512),
        ("hf:Qwen/Qwen3-Embedding-0.6B", 512),
        ("bedrock:titan", 8192),
        ("hashing", None),
        ("unknown:model", None),
    ],
)
def test_input_window_per_backend(backend, window):
    assert input_window(backend) == window


def test_overflow_warning_when_prefix_and_chunk_exceed_the_window(
    tagged_store, monkeypatch
):
    assert compute_tag_report(tagged_store, ALL_TAGS).warnings == []
    monkeypatch.setattr(tag_stats, "input_window", lambda backend: 10)
    warnings = compute_tag_report(tagged_store, ALL_TAGS).warnings
    assert len(warnings) == 1
    assert "16 of 16 chunks" in warnings[0]
    assert "10-token" in warnings[0]


def test_tags_verb_prints_the_report(tmp_path, tagged_folder):
    db = str(tmp_path / "cli.duckdb")
    base = ["--db", db, "--backend", "hashing"]
    runner.invoke(app, ["index", str(tagged_folder), *base])
    args = ["tags", *base, "--tag-fields", "kind: $.kind"]
    args += ["--tag-fields", "product: $.product.name"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    verdicts = {tag["label"]: tag["verdict"] for tag in payload["tags"]}
    assert verdicts == {"kind": "decorative", "product": "effective"}


def test_tags_verb_without_tags_explains_what_to_do(tmp_path, tagged_folder):
    db = str(tmp_path / "cli.duckdb")
    base = ["--db", db, "--backend", "hashing"]
    runner.invoke(app, ["index", str(tagged_folder), *base])
    result = runner.invoke(app, ["tags", *base])
    assert result.exit_code != 0
    assert "No tags declared" in result.output
