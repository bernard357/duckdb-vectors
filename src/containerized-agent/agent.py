"""A tiny answering agent over the duckdb-vectors documentation.

The agent indexes the project's own ``documentation/`` folder into a local
DuckDB file at build time, then answers questions by retrieving the most
relevant chunks. It uses the ``fastembed`` backend (fastembed ONNX, no torch) —
the minimum viable setup for real semantic relevance, running fully offline
once the model is baked into the image. Hand the retrieved chunks to an LLM to
generate prose answers; swap ``backend`` for ``bedrock:titan`` for cloud
embedding.

Build-time:  python agent.py --index <docs-folder> --db <file>
Query-time:  python agent.py --db <file> "your question"
"""

import argparse

from duckdb_vectors import VectorStore, index_folder

BACKEND = "fastembed:minishlab/potion-multilingual-128M"


def build_index(docs: str, db_path: str) -> None:
    """Index a folder of markdown into the DuckDB file."""
    store = VectorStore(db_path=db_path, backend=BACKEND)
    try:
        report = index_folder(store, docs, recursive=True)
        print(
            f"indexed {report.files} files, {report.chunks} chunks "
            f"({report.embedded} embedded)"
        )
    finally:
        store.close()


def answer(question: str, db_path: str, top_k: int = 5) -> list[dict]:
    """Return the top-k retrieved chunks for a question.

    A real agent would pass these chunks to an LLM as grounding; here we return
    them directly so the example stays dependency-free and testable.
    """
    store = VectorStore(db_path=db_path, backend=BACKEND)
    try:
        hits = store.query(question, top_k=top_k)
        return [
            {
                "score": hit.score,
                "source": hit.chunk.source,
                "title": hit.chunk.title,
                "text": hit.chunk.text,
            }
            for hit in hits
        ]
    finally:
        store.close()


def _print_hits(hits: list[dict]) -> None:
    if not hits:
        print("  (no matches)")
        return
    for hit in hits:
        snippet = hit["text"][:200].replace("\n", " ")
        print(f"  [{hit['score']:.4f}] {hit['source']} — {hit['title']}")
        print(f"      {snippet}")


def chat(db_path: str, top_k: int = 5) -> None:
    """Interactive loop: read a question, print the top retrieved chunks."""
    store = VectorStore(db_path=db_path, backend=BACKEND)
    print("duckdb-vectors doc agent. Ask a question, or type 'exit' to quit.")
    try:
        while True:
            try:
                question = input("\n> ").strip()
            except EOFError:
                break
            if not question:
                continue
            if question.lower() in {"exit", "quit"}:
                break
            hits = store.query(question, top_k=top_k)
            _print_hits(
                [
                    {
                        "score": hit.score,
                        "source": hit.chunk.source,
                        "title": hit.chunk.title,
                        "text": hit.chunk.text,
                    }
                    for hit in hits
                ]
            )
    finally:
        store.close()
        print("\nbye.")


def main() -> None:
    parser = argparse.ArgumentParser(description="duckdb-vectors doc agent")
    parser.add_argument("question", nargs="?", help="Question to answer.")
    parser.add_argument("--index", help="Folder of markdown to index.")
    parser.add_argument("--db", default="vectors.duckdb", help="DuckDB file.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--chat", action="store_true", help="Interactive chat loop.")
    args = parser.parse_args()

    if args.index:
        build_index(args.index, args.db)
        return

    if args.chat:
        chat(args.db, args.top_k)
        return

    if not args.question:
        parser.error("provide a question, --chat, or --index")

    _print_hits(answer(args.question, args.db, args.top_k))


if __name__ == "__main__":
    main()
