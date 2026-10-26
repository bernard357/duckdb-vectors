# Communication kit for package discovery

This page is a reusable kit for spreading `duckdb-vectors` across both human and agentic audiences.
Beyond automated discovery, we maintain a communication package and a list of referencing places for `duckdb-vectors`.
For the full picture of what you are registering, start from the [documentation home](index.md).

## Automated discovery

Two discovery conventions are already served from the project itself, so most venues can point at them rather than restate the content:

- __`llms.txt`__ — a curated, Markdown index for LLMs at the root of the documentation endpoint: [llms.txt](https://bernard357.github.io/duckdb-vectors/llms.txt). The convention, proposed by Jeremy Howard in 2024, is an H1 project name, a one-line blockquote summary, then H2 sections of annotated Markdown links; it curates rather than dumps, and it does not control crawling. See [llmstxt.org](https://llmstxt.org/). We ship `llms.txt` only — `llms-full.txt` is deliberately out of scope.

- __`AGENTS.md`__ — an agent-facing Markdown file sitting repository top-level: [AGENTS.md](https://github.com/bernard357/duckdb-vectors/blob/main/AGENTS.md), that can be used jointly by crawling agents and by agentic IDEs. It is the agreed "README for agents" standard that most coding agents now read. See [agents.md](https://agents.md/).

## Working a reference

Working on a new reference to `duckdb-vectors` consists of following steps:

1. Scope a suitable registry or reference where it would make sense to list `duckdb-vectors`
2. If this reference is already present in the table below, and tagged as `complete`, stop here. Maybe you would like to consider another reference target.
3. __candidate → on-going.__ Open a pull request that updates this page, mentioning what you are working on, then do whatever is necessary to register `duckdb-vectors` at the referencing site. Read the venue's current submission rules at its link in the table below (they drift), then prepare the submission from the registration package. For the official MCP registry, the server manifest is already authored at the repo root — [`server.json`](https://github.com/bernard357/duckdb-vectors/blob/main/server.json) (name `io.github.bernard357/duckdb-vectors`, PyPI package with the `mcp` stdio runtime). Re-validate it against the current `$schema` (the schema version drifts), then publish it with the `mcp-publisher` CLI, authenticating the `io.github.bernard357` namespace via GitHub. For the Docker MCP Catalog, fork [docker/mcp-registry](https://github.com/docker/mcp-registry), add the server definition, and open a pull request; every submission gets a Docker-team review before merge, and on approval Docker builds, signs, and publishes the image. For awesome-lists and framework directories, open a pull request adding one annotated link.
4. __on-going.__ The submission is open and awaiting third-party review or merge. Record the pull-request or submission URL here next to the venue.
5. __on-going → complete.__ Complete the pull request: update this page, then ask for a merge. The entry is live in the venue. Verify by loading the public listing, not by trusting the merge — confirm the entry resolves and the links in it work.

Keep this page honest: only mark a venue `complete` once its public listing is live and verified.

## The registration package

Copy-paste these fields into any submission form, catalog entry, or pull request that helps referencing `duckdb-vectors`.

- __Name:__ `duckdb-vectors`
- __One-line pitch:__ A frugal, embeddable semantic-search building block for agentic systems — hybrid vector + exact-token search over a local DuckDB file, no external vector database.
- __Description:__ `duckdb-vectors` indexes a modest, read-mostly corpus into a single DuckDB file and answers hybrid semantic + exact-token queries with a local function call in tens of milliseconds. The file bakes into the agent's own container image, so there is no external vector database to run, pay for, or keep alive. Embedding and chunking are pluggable; the surface is S3-Vectors-flavored. It also ships an MCP-over-stdio server so any MCP client can call its read-only retrieval tools.
- __Keywords:__ duckdb, vectors, embeddings, semantic-search, rag, bm25, agents, mcp, llm, vector-search
- __Install:__ `pip install duckdb-vectors` (add `[bedrock]` for the Amazon Titan embedder)
- __MCP tool:__ `duckdb-vectors mcp` is an MCP-over-stdio server exposing two query-only tools — `search(query, top_k=5)` for hybrid semantic + keyword retrieval over the knowledge base (JSON ranked chunks with metadata) and `report()` for knowledge-base introspection. An MCP client spawns it as a child process and talks to it over stdin/stdout.
- __License:__ MIT
- __Links:__
  - PyPI: <https://pypi.org/project/duckdb-vectors/>
  - Documentation: <https://bernard357.github.io/duckdb-vectors/>
  - Repository: <https://github.com/bernard357/duckdb-vectors>
  - Agent instructions: [AGENTS.md](https://github.com/bernard357/duckdb-vectors/blob/main/AGENTS.md)
  - MCP manifest: [server.json](https://github.com/bernard357/duckdb-vectors/blob/main/server.json)

## External references

Each venue is a place where the package can be discovered. Use the  `candidate` → `on-going` → `complete` lifecycle. Every `candidate` requires a manual maintainer or agent action, pending third-party review. Moving to `complete` requires an accepted review.

| Venue | Type | Status |
| --- | --- | --- |
| [PyPI](https://pypi.org/) | Package metadata | [complete](https://pypi.org/project/duckdb-vectors/) |
| [Official MCP registry](https://registry.modelcontextprotocol.io/) | MCP server catalog | candidate |
| [Docker MCP Catalog](https://github.com/docker/mcp-registry) | MCP server catalog | candidate |
| [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) | Curated awesome-list | candidate |
| [awesome-rag](https://github.com/coree/awesome-rag) | Curated awesome-list | candidate |
| [LangChain integrations](https://python.langchain.com/docs/integrations/providers/) | Agent-framework tool directory | candidate |
| [LlamaIndex integrations](https://llamahub.ai/) | Agent-framework tool directory | candidate |

The candidates listed here are only suggestions. Please add any other reference you believe is worth the effort to spread `duckdb-vectors`.