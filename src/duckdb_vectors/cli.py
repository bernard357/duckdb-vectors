"""Command-line interface for duckdb-vectors.

Three verbs: ``index`` a folder of markdown, ``report`` on database content,
and ``search`` the index. All honor the ``DUCKDB_VECTORS_*`` environment
variables and the ``--db`` / ``--backend`` overrides.
"""

import importlib.metadata
import json as json_module
from enum import StrEnum

import typer
import yaml

from duckdb_vectors.indexer import index_folder
from duckdb_vectors.store import VectorStore

app = typer.Typer(
    help="Embeddable semantic search over a local DuckDB database.",
    no_args_is_help=True,
    add_completion=False,
)


class OutputFormat(StrEnum):
    """Structured output formats accepted by ``--output`` (default json)."""

    json = "json"
    yaml = "yaml"


_OUTPUT_OPTION = typer.Option(
    OutputFormat.json, "--output", help="Structured output format: json or yaml."
)


def _emit(data: object, output: OutputFormat) -> None:
    """Print ``data`` as JSON (default) or YAML on stdout.

    Both formats serialize the same plain data, so they never diverge. JSON is
    the standard for every invocation; YAML is offered for readability.
    """
    if output is OutputFormat.yaml:
        typer.echo(yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip())
    else:
        typer.echo(json_module.dumps(data, indent=2))


def _version() -> str:
    """Return the installed, tag-derived package version."""
    try:
        return importlib.metadata.version("duckdb-vectors")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(_version())
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        is_eager=True,
        help="Show the version and exit.",
        callback=_version_callback,
    ),
) -> None:
    """Embeddable semantic search over a local DuckDB database."""


@app.command()
def index(
    folder: str = typer.Argument(..., help="Folder of markdown files."),
    db: str | None = typer.Option(None, "--db", help="DuckDB file path."),
    backend: str | None = typer.Option(
        None, "--backend", help="Embedding backend spec."
    ),
    recursive: bool = typer.Option(
        False, "--recursive", help="Recurse into sub-folders."
    ),
    output: OutputFormat = _OUTPUT_OPTION,
) -> None:
    """Index a folder of markdown into the database."""
    store = VectorStore(db_path=db, backend=backend)
    try:
        report = index_folder(store, folder, recursive=recursive)
    finally:
        store.close()
    _emit(report.model_dump(), output)


@app.command()
def report(
    db: str | None = typer.Option(None, "--db", help="DuckDB file path."),
    backend: str | None = typer.Option(
        None, "--backend", help="Embedding backend spec."
    ),
    output: OutputFormat = _OUTPUT_OPTION,
) -> None:
    """Report on database content."""
    store = VectorStore(db_path=db, backend=backend)
    try:
        chunks = store.list_vectors()
        sources = sorted({chunk.source for chunk in chunks if chunk.source})
        payload = {
            "db_path": store.settings.db_path,
            "backend": store.settings.backend,
            "dimensions": store.dimensions,
            "chunks": len(chunks),
            "sources": sources,
        }
    finally:
        store.close()
    _emit(payload, output)


@app.command()
def mcp(
    db: str | None = typer.Option(None, "--db", help="DuckDB file path."),
    backend: str | None = typer.Option(
        None, "--backend", help="Embedding backend spec."
    ),
    index: str | None = typer.Option(
        None,
        "--index",
        help="Folder to index into the database before serving.",
    ),
    recursive: bool = typer.Option(
        False, "--recursive", help="Recurse into sub-folders when indexing."
    ),
) -> None:
    """Serve retrieval as an MCP server over stdio.

    With ``--index <folder>`` the server indexes that folder into ``--db``
    before serving, so a client can launch a fully self-contained server with
    no separate indexing step. Indexing is incremental, so an unchanged folder
    is near-instant on restart.
    """
    from duckdb_vectors.config import Settings
    from duckdb_vectors.mcp_server import run_stdio

    run_stdio(
        Settings(db_path=db, backend=backend),
        index_folder=index,
        recursive=recursive,
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="Query text."),
    db: str | None = typer.Option(None, "--db", help="DuckDB file path."),
    backend: str | None = typer.Option(
        None, "--backend", help="Embedding backend spec."
    ),
    top_k: int = typer.Option(5, "--top-k", help="Number of results."),
    output: OutputFormat = _OUTPUT_OPTION,
) -> None:
    """Hybrid search the index."""
    store = VectorStore(db_path=db, backend=backend)
    try:
        results = store.query(query, top_k=top_k)
    finally:
        store.close()
    _emit([result.model_dump() for result in results], output)


if __name__ == "__main__":
    app()
