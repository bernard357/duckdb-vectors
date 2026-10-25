"""CLI smoke tests for index / report / search via CliRunner."""

import importlib.metadata

from typer.testing import CliRunner

from duckdb_vectors.cli import app

runner = CliRunner()

DOC = "# Title\n\nDuckDB is an in-process analytical database.\n"


def test_index_report_search_flow(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "intro.md").write_text(DOC)
    db = str(tmp_path / "cli.duckdb")

    indexed = runner.invoke(
        app, ["index", str(docs), "--db", db, "--backend", "hashing"]
    )
    assert indexed.exit_code == 0, indexed.output
    assert "Indexed" in indexed.output

    reported = runner.invoke(
        app, ["report", "--db", db, "--backend", "hashing", "--json"]
    )
    assert reported.exit_code == 0, reported.output
    assert '"chunks"' in reported.output

    searched = runner.invoke(
        app,
        [
            "search",
            "analytical database",
            "--db",
            db,
            "--backend",
            "hashing",
            "--top-k",
            "3",
        ],
    )
    assert searched.exit_code == 0, searched.output
    assert "intro.md" in searched.output


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
