# HOL-Guard MCP contribution — staging reference

Reference material for contributing the `duckdb-vectors` stdio MCP server to
[HOL-Guard](https://github.com/hashgraph-online/hol-guard) as an __MCP server
contribution__. These files are produced from this repository's MCP server and
staged here; the canonical contribution is generated, reviewed, and bound inside
a HOL-Guard checkout with its `hol-guard extensions` CLI (not present in this
repo).

## Files

- `duckdb-vectors-tools.json` — the MCP tool inventory: a complete JSON-RPC 2.0
  `tools/list` result for the two exposed tools (`search`, `report`), captured
  from `src/duckdb_vectors/mcp_server.py` via the server's own `list_tools()`.
  Single page, no pagination. Regenerate it from the live server if the tool
  surface changes.
- `duckdb-vectors-review.json` — the reviewed per-tool policy draft: a `state`
  (`allow`/`review`/`block`/`inherit`), a `rationale`, and an HTTPS `evidence`
  URL per tool. Both tools are read-only, so both are `allow`; the server
  publishes no MCP annotations, so classification is justified from the source.

## What is NOT here

The HOL-Guard CLI and a HOL-Guard checkout. The remaining steps —
`hol-guard extensions generate --from mcp` / `validate` / review / `generate
--from snapshot` / `diff` / `apply` / draft PR — run from a HOL-Guard checkout
using these files as inputs.

## Identity

- Launcher: `uvx` · Package: `duckdb-vectors`
- Homepage: <https://github.com/bernard357/duckdb-vectors>
- Upstream version: `261026`
- Publisher: `community.bernardpaques` (Bernard Paques)

## Regenerating the inventory

The inventory is the server's `list_tools()` output wrapped as a JSON-RPC
`tools/list` result. Rebuild it by loading the server
(`duckdb_vectors.mcp_server.build_server`) and serialising each tool's `name`,
`description`, and input schema. Finalize the package version and tool
inventory before the HOL-Guard review: changing source bytes, identity, or
metadata invalidates the review binding.
