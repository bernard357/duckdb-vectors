---
name: search-duckdb-vectors
description: >
  Search a local knowledge base with fast semantic + exact-token (keyword)
  retrieval to ground an answer in indexed documents, using the duckdb-vectors
  package run on demand with uvx (no install).
  Use this whenever the user asks about domain-, project-, or product-specific
  content that goes beyond the agent's general knowledge; whenever a request
  contains acronyms, product codes, identifiers, or other literal keywords that
  must be matched exactly; or whenever the user explicitly asks to search the
  docs, a corpus, a wiki, or a knowledge base.
  Triggers on: "search the knowledge base", "semantic search", "what do the docs
  say about", "look this up in the docs", "find in the corpus", "search our
  documentation", "ground this in the knowledge base", "duckdb-vectors".
  Do NOT use for general-knowledge questions the agent can already answer, for
  web search, or for writing new documents — this skill only retrieves from an
  existing local index.
---

# Search a knowledge base with duckdb-vectors

Give the agent fast, offline semantic + keyword search over a folder of
documents by running [`duckdb-vectors`](https://pypi.org/project/duckdb-vectors/)
with `uvx` — the package is fetched from PyPI and run in the skill's own process
space, so there is no install step. Use it to ground a response in a specific
corpus whenever the answer needs more than the agent's general knowledge.

## Two kinds of search, fused

`duckdb-vectors` runs hybrid retrieval and fuses the two signals:

- __Semantic (vector) search__ — matches by meaning, so a question and a
  relevant passage match even when they share no words. Best for conceptual or
  paraphrased questions.
- __Exact-token (keyword) search__ — matches literal strings, so acronyms,
  product codes, identifiers, and error strings are found verbatim. Best when
  the request hinges on a specific token the agent must not paraphrase.

You do not choose between them — every `search` call runs both and returns one
ranked list. Reach for this skill precisely when a request is conceptual, or
contains keywords, or both.

## Mode 1 — one-shot CLI (quick interactive lookup)

Index the folder once, then search it. Both commands run via `uvx`; output is
JSON by default so the result is machine-parseable.

```shell
$ uvx duckdb-vectors index /abs/path/to/docs --recursive --db /abs/path/to/kb.duckdb
$ uvx duckdb-vectors search "how does the retry policy work?" --db /abs/path/to/kb.duckdb --output json
```

`search` returns a JSON array of ranked results, best first. Each result nests
the matched `chunk` (`chunk_id`, `text`, `source`, `title`, `metadata`) under a
fused `score`. Parse the JSON, read the `text` fields of the top hits, and use
them as grounding context for the answer; cite `source` where useful. Pass
`--output yaml` instead if a human will read the output. Re-running `index` on
an unchanged folder is near-instant (incremental), so it is safe to index
before every search session.

Use this mode for a quick, self-contained lookup from within the skill.

## Mode 2 — MCP over stdio (when the harness speaks MCP)

When the agent harness drives tools over the Model Context Protocol, launch the
server instead of the CLI. It indexes the folder on startup, then serves the
`search` and `report` tools over stdio:

```shell
$ uvx duckdb-vectors mcp --index /abs/path/to/docs --recursive --db /abs/path/to/kb.duckdb
```

The client spawns this as a child process and calls `search(query, top_k)` and
`report()` over stdin/stdout — no network, no port. Use this mode when the agent
already consumes MCP tools and should keep the knowledge base available for the
whole session rather than shelling out per query.

## Checking what the knowledge base contains

Before searching an unfamiliar index, `report` describes it — database path,
embedding backend, vector dimensions, chunk count, and the list of sources — so
the agent can judge whether the corpus is worth searching and what it covers:

```shell
$ uvx duckdb-vectors report --db /abs/path/to/kb.duckdb --output json
```
