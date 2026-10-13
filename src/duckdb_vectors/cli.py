"""Command-line interface for duckdb-vectors.

Three verbs: ``index`` a folder of markdown, ``report`` on database content,
and ``search`` the index. All honor the ``DUCKDB_VECTORS_*`` environment
variables and the ``--db`` / ``--backend`` overrides.
"""

import json as json_module

import typer

from duckdb_vectors.indexer import index_folder
from duckdb_vectors.store import VectorStore

app = typer.Typer(
    help="Embeddable semantic search over a local DuckDB database.",
    no_args_is_help=True,
    add_completion=False,
)


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
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Index a folder of markdown into the database."""
    store = VectorStore(db_path=db, backend=backend)
    try:
        report = index_folder(store, folder, recursive=recursive)
    finally:
        store.close()
    if as_json:
        typer.echo(report.model_dump_json())
    else:
        typer.echo(
            f"Indexed {report.files} files, {report.chunks} chunks "
            f"({report.embedded} embedded, {report.reused} reused, "
            f"{report.pruned} pruned)."
        )


@app.command()
def report(
    db: str | None = typer.Option(None, "--db", help="DuckDB file path."),
    backend: str | None = typer.Option(
        None, "--backend", help="Embedding backend spec."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Report on database content."""
    store = VectorStore(db_path=db, backend=backend, read_only=True)
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
    if as_json:
        typer.echo(json_module.dumps(payload, indent=2))
    else:
        typer.echo(f"Database : {payload['db_path']}")
        typer.echo(f"Backend  : {payload['backend']}")
        typer.echo(f"Dimension: {payload['dimensions']}")
        typer.echo(f"Chunks   : {payload['chunks']}")
        typer.echo(f"Sources  : {len(sources)}")
        for source in sources:
            typer.echo(f"  - {source}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Query text."),
    db: str | None = typer.Option(None, "--db", help="DuckDB file path."),
    backend: str | None = typer.Option(
        None, "--backend", help="Embedding backend spec."
    ),
    top_k: int = typer.Option(5, "--top-k", help="Number of results."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Hybrid search the index."""
    store = VectorStore(db_path=db, backend=backend, read_only=True)
    try:
        results = store.query(query, top_k=top_k)
    finally:
        store.close()
    if as_json:
        typer.echo(
            json_module.dumps([result.model_dump() for result in results], indent=2)
        )
    else:
        for result in results:
            snippet = result.chunk.text[:80].replace("\n", " ")
            typer.echo(f"{result.score:.4f}  {result.chunk.chunk_id}  {snippet}")


if __name__ == "__main__":
    app()
