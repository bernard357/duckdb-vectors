"""CLI smoke tests for index / report / search via CliRunner."""

import importlib.metadata
import json

import yaml
from typer.testing import CliRunner

from duckdb_vectors.cli import app

runner = CliRunner()

DOC = "# Title\n\nDuckDB is an in-process analytical database.\n"


def test_index_report_search_flow_defaults_to_json(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "intro.md").write_text(DOC)
    db = str(tmp_path / "cli.duckdb")

    # No --output flag: every verb emits JSON by default.
    indexed = runner.invoke(
        app, ["index", str(docs), "--db", db, "--backend", "hashing"]
    )
    assert indexed.exit_code == 0, indexed.output
    assert json.loads(indexed.output)["chunks"] >= 1

    reported = runner.invoke(app, ["report", "--db", db, "--backend", "hashing"])
    assert reported.exit_code == 0, reported.output
    assert json.loads(reported.output)["chunks"] >= 1

    searched = runner.invoke(
        app,
        ["search", "analytical database", "--db", db, "--backend", "hashing"],
    )
    assert searched.exit_code == 0, searched.output
    hits = json.loads(searched.output)
    # search emits full SearchResult dumps: the chunk is nested under "chunk".
    assert hits and hits[0]["chunk"]["source"] == "intro.md"


def test_output_yaml(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "intro.md").write_text(DOC)
    db = str(tmp_path / "cli.duckdb")
    runner.invoke(app, ["index", str(docs), "--db", db, "--backend", "hashing"])

    reported = runner.invoke(
        app, ["report", "--db", db, "--backend", "hashing", "--output", "yaml"]
    )
    assert reported.exit_code == 0, reported.output
    payload = yaml.safe_load(reported.output)
    assert payload["backend"] == "hashing"
    assert payload["chunks"] >= 1


def test_invalid_output_value_errors(tmp_path):
    db = str(tmp_path / "cli.duckdb")
    result = runner.invoke(
        app, ["report", "--db", db, "--backend", "hashing", "--output", "toml"]
    )
    assert result.exit_code != 0


def test_version_flag_prints_tag_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == importlib.metadata.version("duckdb-vectors")


def test_version_flag_fallback_when_metadata_missing(monkeypatch):
    def raise_not_found(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(
        "duckdb_vectors.cli.importlib.metadata.version", raise_not_found
    )
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert "0.0.0+unknown" in result.output


def test_search_defaults_to_twenty_results_and_top_k_overrides(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(25):
        (docs / f"page{i}.md").write_text(f"# Page {i}\n\nshared words number{i}\n")
    db = str(tmp_path / "idx.duckdb")
    runner.invoke(app, ["index", str(docs), "--db", db, "--backend", "hashing"])
    args = ["search", "shared words", "--db", db, "--backend", "hashing"]
    assert len(json.loads(runner.invoke(app, args).output)) == 20
    limited = runner.invoke(app, [*args, "--top-k", "3"])
    assert len(json.loads(limited.output)) == 3


def test_index_with_tag_fields_shows_the_projection_in_the_report(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("---\nproduct: Atlas\n---\n# A\n\nbody text\n")
    db = str(tmp_path / "idx.duckdb")
    base = ["--db", db, "--backend", "hashing"]
    indexed = runner.invoke(
        app, ["index", str(docs), *base, "--tag-fields", "product: $.product"]
    )
    assert indexed.exit_code == 0, indexed.output
    plain = json.loads(runner.invoke(app, ["report", *base]).output)
    assert plain["tag_fields"] == []
    env = {"DUCKDB_VECTORS_TAG_FIELDS": "product: $.product"}
    tagged = runner.invoke(app, ["report", *base], env=env)
    assert json.loads(tagged.output)["tag_fields"] == ["product: $.product"]
    bad = runner.invoke(app, ["index", str(docs), *base, "--tag-fields", "nonsense"])
    assert bad.exit_code != 0
