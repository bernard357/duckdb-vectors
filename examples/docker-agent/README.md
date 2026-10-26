# Docker Agent example

A runnable [Docker Agent](https://docs.docker.com/ai/docker-agent/) configuration
that wires an agent to `duckdb-vectors` through an `mcp` local-stdio toolset.
Docker Agent spawns `duckdb-vectors mcp` as a subprocess and calls its `search`
and `report` tools over stdin/stdout.

See the [Docker Agent guide](https://github.com/bernard357/duckdb-vectors/blob/main/documentation/docker-agent-guide.md)
for the full walk-through.

## Prerequisites

- Docker Agent installed (`docker agent` available on your PATH).
- `duckdb-vectors` installed on your PATH (`pip install duckdb-vectors`).
- An `ANTHROPIC_API_KEY` in your environment (or edit `agent.yaml` to use
  another provider).

## Run it

Build an index, then point the agent at it:

```shell
$ pip install duckdb-vectors
$ duckdb-vectors index ./documentation --db vectors.duckdb
$ export DUCKDB_VECTORS_DB_PATH="$PWD/vectors.duckdb"
$ docker agent run examples/docker-agent/agent.yaml "how is a search answered?"
```

The agent calls the `search` tool, which opens the index and returns
ranked chunks; the model grounds its answer in them.

## Configuration

`agent.yaml` declares one agent with a single `mcp` toolset:

- `command: duckdb-vectors` and `args: ["mcp"]` start the stdio server.
- `tools: ["search", "report"]` whitelists the exposed tools.
- `env:` points the server at the index via `DUCKDB_VECTORS_DB_PATH`.
