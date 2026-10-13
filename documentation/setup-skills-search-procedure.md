# Setup skills-search procedure

A step-by-step procedure to expose a folder of agent skills to an agent through
semantic search. Instead of listing every skill in the prompt, the agent
searches a `duckdb-vectors` index and selects the few skills relevant to the
task — scaling to hundreds of skills without inflating context.

- [Why search skills instead of listing them](#why)
- [Step 1 — gather the skills](#step-1)
- [Step 2 — index skill descriptions](#step-2)
- [Step 3 — expose a selection tool](#step-3)
- [Step 4 — wire the tool into the agent](#step-4)
- [Step 5 — runtime append for dynamic skills](#step-5)
- [Follow-up](#follow-up)

## Why search skills instead of listing them {#why}

Listing hundreds of skills in every prompt is costly and dilutes the model's
attention. A better pattern: index each skill's name and description once, and
at task time retrieve the handful that match the user's intent. The agent then
sees only relevant skills. This is tool-selection by semantic search, and it is
exactly the small-corpus, fast-retrieval case `duckdb-vectors` targets.

## Step 1 — gather the skills {#step-1}

Assume a folder where each skill has a markdown description (a `SKILL.md` or
similar) with a name and a short summary of what it does and when to use it:

```text
skills/
  web-search/SKILL.md
  sql-query/SKILL.md
  send-email/SKILL.md
```

If your skills are defined in code or YAML, generate one short markdown
description per skill first — the richer the "what it does / when to use it"
text, the better the semantic match.

## Step 2 — index skill descriptions {#step-2}

Index the skills folder. Each skill becomes searchable by meaning; the skill id
lives in the chunk source and metadata:

```python
from duckdb_vectors import VectorStore

SKILLS = [
    ("skill:web-search", "Search the public web and return ranked results."),
    ("skill:sql-query", "Run read-only SQL queries against a warehouse."),
    ("skill:send-email", "Compose and send an email to a recipient."),
]

store = VectorStore(db_path="skills.duckdb", backend="local")
store.add_texts(
    ids=[sid for sid, _ in SKILLS],
    texts=[desc for _, desc in SKILLS],
    metadatas=[{"kind": "skill"} for _ in SKILLS],
)
store.close()
```

A folder of `SKILL.md` files can be indexed the same way with
`index_folder(store, "skills", recursive=True)`.

## Step 3 — expose a selection tool {#step-3}

Wrap retrieval as a function the agent can call to find relevant skills:

```python
store = VectorStore(db_path="skills.duckdb", backend="local", read_only=True)


def find_skills(task: str, k: int = 3) -> list[str]:
    """Return the ids of the skills most relevant to a task."""
    return [hit.chunk.chunk_id for hit in store.query(task, top_k=k)]
```

```python
find_skills("I need to look something up online")
# ["skill:web-search", ...]
```

## Step 4 — wire the tool into the agent {#step-4}

Register `find_skills` as a tool in your harness. The agent calls it with the
user's task, gets back a short list of candidate skill ids, loads only those
skill definitions, and proceeds. The prompt never carries the full catalog —
only the selected skills.

This keeps context small and selection fast: a few hundred skills still answer
in tens of milliseconds, and adding skills does not grow the prompt.

## Step 5 — runtime append for dynamic skills {#step-5}

If the skill set is assembled at harness startup (plugins, per-tenant skills),
open the store read-write at init and append them before serving — the
runtime-append lifecycle:

```python
store = VectorStore(db_path="skills.duckdb", backend="local")
store.add_texts(ids=[...], texts=[...], metadatas=[...])
# then switch to read-only queries for the rest of the session
```

Keep writes to one process — a DuckDB file is single-writer. For a fixed skill
set, prefer indexing once at build time and opening read-only.

## Follow-up {#follow-up}

- [setup-rag-procedure.md](setup-rag-procedure.md) — the document-RAG procedure
- [duckdb-vectors-guide.md](duckdb-vectors-guide.md) — harness integration
- [examples/skill_registry.py](../examples/skill_registry.py) — a runnable registry
