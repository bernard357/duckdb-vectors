"""Index and query with the core ``hf:`` Hugging Face ONNX backend.

The ``hf:<repo-id>`` backend loads any Hugging Face repository that ships ONNX
weights and a ``tokenizer.json`` — here ``Qwen/Qwen3-Embedding-0.6B`` — through
the ONNX runtime fastembed already vendors (no torch, no transformers). The
loader mean-pools by default and switches to last-token pooling for Qwen3-style
repos automatically.

The first run downloads the model from the Hugging Face Hub; later runs are
offline from the local cache. Repos that do not publish ONNX weights are not
loadable by this backend — pick a repo with an exported ``onnx/model.onnx``.

Build-time:  python agent.py --index <docs-folder> --db <file>
Query-time:  python agent.py --db <file> "your question"
"""

import argparse

from duckdb_vectors import VectorStore, index_folder

BACKEND = "hf:Qwen/Qwen3-Embedding-0.6B"


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
    """Return the top-k retrieved chunks for a question."""
    store = VectorStore(db_path=db_path, backend=BACKEND, read_only=True)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="duckdb-vectors hf: backend demo")
    parser.add_argument("question", nargs="?", help="Question to answer.")
    parser.add_argument("--index", help="Folder of markdown to index.")
    parser.add_argument("--db", default="vectors.duckdb", help="DuckDB file.")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    if args.index:
        build_index(args.index, args.db)
        return

    if not args.question:
        parser.error("provide a question or --index")

    _print_hits(answer(args.question, args.db, args.top_k))


if __name__ == "__main__":
    main()
