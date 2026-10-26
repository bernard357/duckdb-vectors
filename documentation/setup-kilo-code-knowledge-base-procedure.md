# Give Kilo Code a local knowledge base

Wire your own folder of documents into [Kilo Code](https://kilo.ai) — the agentic extension for VS Code — so the assistant can search it while you work, without sending anything to a cloud retrieval service. Kilo Code is an MCP client; `duckdb-vectors mcp` is an MCP server over stdio. Point one at the other and the assistant gains a read-only `search` tool over your indexed documents.

This is the __plug into an existing editor__ path. The index is a single DuckDB file on your disk, and the MCP server runs as a local subprocess Kilo Code spawns — no network on the query path.

```mermaid
flowchart TD
    You[question in Kilo Code] --> Kilo[Kilo Code / VS Code]
    Kilo -->|spawn: duckdb-vectors mcp| MCP[MCP stdio server]
    MCP <--> DB[(vectors.duckdb)]
    MCP -->|ranked chunks| Kilo
    Kilo -->|grounded answer| You
```

## Step 1 — gather a folder of documents

Collect the documents you want Kilo Code to draw on into one folder. Indexing defaults to the composite chunker, which dispatches each file to the registered chunker claiming its MIME type: the markdown chunker splits markdown on headings and the PDF chunker reads PDFs directly, so both are first-class; convert other formats (HTML, office documents) to markdown first.

```text
knowledge/
  standards/python.md
  design/retrieval-notes.md
  runbooks/deploy.md
```

A local folder is all that is needed at index time — it can be a working copy, a git checkout, or a wiki export.

## Step 2 — index the folder

Install the package from [PyPI](https://pypi.org/project/duckdb-vectors/) (the `mcp` verb ships in the base install) and build a single DuckDB file from the folder:

```shell
$ pip install duckdb-vectors
$ duckdb-vectors index ./knowledge --recursive --db knowledge.duckdb
```

The first run downloads the default local embedding model; later runs reuse the cache. Check what landed with the `report` verb:

```shell
$ duckdb-vectors report --db knowledge.duckdb
```

## Step 3 — add the MCP server to Kilo Code

Kilo Code reads MCP servers from a JSON config. Add a server that spawns `duckdb-vectors mcp` over stdio and points it at the index you built. Use an __absolute__ database path, because Kilo Code spawns the subprocess with its own working directory:

```json
{
  "mcpServers": {
    "knowledge": {
      "command": "duckdb-vectors",
      "args": ["mcp"],
      "env": {
        "DUCKDB_VECTORS_DB_PATH": "/absolute/path/to/knowledge.duckdb"
      }
    }
  }
}
```

A few things to get right:

- __Use the `mcpServers` key__, not VS Code's native `servers` key. Kilo Code follows the Claude/Cursor convention; a snippet copied from VS Code's own MCP documentation will not register.
- __Scope.__ Put this in the project-level config so the knowledge base travels with the workspace, or in the global (user-level) config for a personal library shared across projects. Kilo Code's [MCP documentation](https://kilo.ai/docs/automate/mcp/using-in-kilo-code) gives the exact file name and location for each scope — check it, as the extension owns these paths.
- __`command` on PATH vs an absolute venv path.__ `"duckdb-vectors"` works when the package is installed on the PATH the extension inherits. VS Code does not always inherit a shell PATH, so if the server fails to start, pin the absolute venv binary instead:

```json
"command": "/absolute/path/to/.venv/bin/duckdb-vectors"
```

## Step 4 — confirm it works

After saving the config, Kilo Code starts the server and lists its tools. `duckdb-vectors mcp` registers two read-only tools:

- `search(query, top_k=5)` — hybrid semantic + exact-token search; returns ranked chunks (`chunk_id`, `text`, `source`, `title`, `score`).
- `report()` — the index's database path, backend, dimensions, chunk count, and sources.

Ask the assistant something your documents answer and confirm it calls `search` and grounds its reply in the returned chunks. You can also verify the server independently of Kilo Code:

```shell
$ DUCKDB_VECTORS_DB_PATH="$PWD/knowledge.duckdb" duckdb-vectors report
```

## Keeping the index fresh

The index is a static file, built once; edits to the source folder are not picked up live. When the documents change, re-run the indexer and reconnect the server:

```shell
$ duckdb-vectors index ./knowledge --recursive --db knowledge.duckdb
```

The running MCP server opens the file read-only, so reconnect (or restart) it in Kilo Code after re-indexing to serve the updated content.
