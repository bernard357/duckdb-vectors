"""Runtime-append use case: a registry of tools or skills built at harness init.

Unlike the frozen-docs case, the store opens read-write and the harness appends
its skills on startup, then selects relevant skills by semantic search. A small
registry of hundreds of entries stays well within the tens-of-milliseconds
search budget.
"""

from duckdb_vectors import VectorStore

SKILLS = [
    ("skill:web-search", "Search the public web and return ranked results."),
    ("skill:sql-query", "Run read-only SQL queries against a warehouse."),
    ("skill:send-email", "Compose and send an email to a recipient."),
    ("skill:calendar", "Create, move, and cancel calendar events."),
    ("skill:translate", "Translate text between natural languages."),
]


def build_registry(db_path: str = "skills.duckdb") -> VectorStore:
    """Open a store and (idempotently) append the skill registry."""
    store = VectorStore(
        db_path=db_path, backend="fastembed:minishlab/potion-multilingual-128M"
    )
    store.add_texts(
        ids=[skill_id for skill_id, _ in SKILLS],
        texts=[description for _, description in SKILLS],
        metadatas=[{"kind": "skill"} for _ in SKILLS],
    )
    return store


def main() -> None:
    store = build_registry()
    try:
        results = store.query("I need to look something up online", top_k=3)
        for result in results:
            print(f"{result.score:.4f}  {result.chunk.chunk_id}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
