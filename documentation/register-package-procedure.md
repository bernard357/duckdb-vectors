# Foster adoption: register duckdb-vectors in discovery venues

This page is a reusable kit for spreading `duckdb-vectors` to the registries and directories where humans and agents discover packages. A maintainer or an autonomous agent can replay it: copy the registration package below, then work the venue table, moving each venue through the `candidate` → `on-going` → `complete` lifecycle.

Two discovery conventions are already served from the project itself, so most venues can point at them rather than restate the content:

- __`llms.txt`__ — a curated, Markdown index for LLMs at the site root (<https://bernard357.github.io/duckdb-vectors/llms.txt>). The convention, proposed by Jeremy Howard in 2024, is an H1 project name, a one-line blockquote summary, then H2 sections of annotated Markdown links; it curates rather than dumps, and it does not control crawling. See [llmstxt.org](https://llmstxt.org/). We ship `llms.txt` only — `llms-full.txt` is deliberately out of scope.
- __`AGENTS.md`__ — a top-level, agent-facing Markdown file describing how an agent adopts the package (install, SDK entry point, MCP tool). It is the agreed "README for agents" standard that most coding agents now read. See [agents.md](https://agents.md/).

## The registration package

Copy-paste these fields into any submission form, catalog entry, or pull request.

- __Name:__ `duckdb-vectors`
- __One-line pitch:__ A frugal, embeddable semantic-search building block for agentic systems — hybrid vector + exact-token search over a local DuckDB file, no external vector database.
- __Description:__ `duckdb-vectors` indexes a modest, read-mostly corpus into a single DuckDB file and answers hybrid semantic + exact-token queries with a local function call in tens of milliseconds. The file bakes into the agent's own container image, so there is no external vector database to run, pay for, or keep alive. Embedding and chunking are pluggable; the surface is S3-Vectors-flavored. It also ships an MCP-over-stdio server so any MCP client can call its read-only retrieval tools.
- __Keywords:__ duckdb, vectors, embeddings, semantic-search, rag, bm25, agents, mcp, llm, vector-search
- __Install:__ `pip install duckdb-vectors` (add `[bedrock]` for the Amazon Titan embedder)
- __MCP tool:__ `duckdb-vectors mcp` is an MCP-over-stdio server exposing two read-only tools — `search(query, top_k=5)` for hybrid retrieval and `report()` for index introspection. An MCP client spawns it as a child process and talks to it over stdin/stdout.
- __License:__ MIT
- __Links:__
  - PyPI: <https://pypi.org/project/duckdb-vectors/>
  - Documentation: <https://bernard357.github.io/duckdb-vectors/>
  - Repository: <https://github.com/bernard357/duckdb-vectors>
  - Agent instructions: [AGENTS.md](https://github.com/bernard357/duckdb-vectors/blob/main/AGENTS.md)
  - MCP manifest: [server.json](https://github.com/bernard357/duckdb-vectors/blob/main/server.json)

## Venue status table

Each venue is a place where the package can be discovered. PyPI is `complete` because the package is live; every other venue is a manual maintainer or agent action, pending third-party review, and stays `candidate` until that review lands.

| Venue | Type | Link | Status |
| --- | --- | --- | --- |
| PyPI | Package metadata | <https://pypi.org/project/duckdb-vectors/> | complete |
| Official MCP registry | MCP server catalog | <https://registry.modelcontextprotocol.io/> | candidate |
| Docker MCP Catalog | MCP server catalog | <https://github.com/docker/mcp-registry> | candidate |
| awesome-mcp-servers | Curated awesome-list | <https://github.com/punkpeye/awesome-mcp-servers> | candidate |
| awesome-rag | Curated awesome-list | <https://github.com/coree/awesome-rag> | candidate |
| LangChain integrations | Agent-framework tool directory | <https://python.langchain.com/docs/integrations/providers/> | candidate |
| LlamaIndex integrations | Agent-framework tool directory | <https://llamahub.ai/> | candidate |

## Working a venue

Move a venue forward in three steps.

1. __candidate → on-going.__ Read the venue's current submission rules at the link above (they drift), then prepare the submission from the registration package. For the official MCP registry, the server manifest is already authored at the repo root — [`server.json`](https://github.com/bernard357/duckdb-vectors/blob/main/server.json) (name `io.github.bernard357/duckdb-vectors`, PyPI package with the `mcp` stdio runtime). Re-validate it against the current `$schema` (the schema version drifts), then publish it with the `mcp-publisher` CLI, authenticating the `io.github.bernard357` namespace via GitHub. For the Docker MCP Catalog, fork [docker/mcp-registry](https://github.com/docker/mcp-registry), add the server definition, and open a pull request; every submission gets a Docker-team review before merge, and on approval Docker builds, signs, and publishes the image. For awesome-lists and framework directories, open a pull request adding one annotated link.
2. __on-going.__ The submission is open and awaiting third-party review or merge. Record the pull-request or submission URL here next to the venue.
3. __on-going → complete.__ The entry is live in the venue. Verify by loading the public listing, not by trusting the merge — confirm the entry resolves and the links in it work.

Keep this page honest: only mark a venue `complete` once its public listing is live and verified.

For the full picture of what you are registering, start from the [documentation home](index.md).
