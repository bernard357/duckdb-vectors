"""CLI smoke tests for index / report / search via CliRunner."""

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
