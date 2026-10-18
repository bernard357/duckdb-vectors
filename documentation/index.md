---
layout: home

hero:
  name: duckdb-vectors
  text: Frugal semantic search for agentic systems
  tagline: An embeddable building block for hybrid vector + exact-token search over a local DuckDB file, with no external vector database.
  actions:
    - theme: brand
      text: Read the primitive
      link: /duckdb-vectors-primitive
    - theme: alt
      text: Install from PyPI
      link: https://pypi.org/project/duckdb-vectors/

features:
  - title: The primitive
    details: Understand the core idea — chunk items, store embeddings in DuckDB, and search with vector distances and BM25 in tens of milliseconds.
    link: /duckdb-vectors-primitive
  - title: User guide
    details: Index a folder of Markdown, report on database content, and run hybrid search from the command line or from your own Python code.
    link: /duckdb-vectors-guide
  - title: Set up a RAG agent
    details: Turn an existing Markdown folder into a Retrieval-Augmented Generation capability baked into the container that packages your agent.
    link: /setup-rag-procedure
  - title: Search over skills
    details: Add a registry of hundreds of skills selected by semantic search instead of exposing every tool to your agent at once.
    link: /setup-skills-search-procedure
---
