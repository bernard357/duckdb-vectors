"""A fully offline RAG agent over the duckdb-vectors documentation.

Retrieves the most relevant documentation chunks with duckdb-vectors, then
assembles a grounded answer with a small local model (Qwen2.5-0.5B-Instruct,
GGUF, via llama-cpp-python). Everything runs offline — the embedding model and
the generation model are baked into the image — so this is the local
counterpart to the Bedrock-backed Lambda and AgentCore agents.

Build-time:  python agent.py --index <docs-folder> --db <file>
Query-time:  python agent.py --db <file> "your question"
Output is a single JSON object: {"answer": ..., "sources": [...]}.
"""

import argparse
import json
import os

from duckdb_vectors import VectorStore, index_folder

BACKEND = os.environ.get(
    "DUCKDB_VECTORS_BACKEND", "fastembed:minishlab/potion-multilingual-128M"
)
# The local generation model, fetched from HuggingFace into the image's cache
# at build time so the running container needs no network.
RAG_MODEL_REPO = os.environ.get("RAG_MODEL_REPO", "Qwen/Qwen2.5-0.5B-Instruct-GGUF")
RAG_MODEL_FILE = os.environ.get("RAG_MODEL_FILE", "*q4_k_m.gguf")
TOP_K = 5

_llm = None


def _load_llm():
    """Lazily load the local GGUF model via llama-cpp-python.

    Uses the HuggingFace cache populated at build time, so no download happens
    at run time. ``warm`` builds exercise this during the image build.
    """
    global _llm
    if _llm is None:
        from llama_cpp import Llama

        _llm = Llama.from_pretrained(
            repo_id=RAG_MODEL_REPO,
            filename=RAG_MODEL_FILE,
            n_ctx=4096,
            n_threads=os.cpu_count(),
            verbose=False,
        )
    return _llm


def build_index(docs: str, db_path: str) -> None:
    """Index a folder of markdown into the DuckDB file."""
    store = VectorStore(db_path=db_path, backend=BACKEND)
    try:
        report = index_folder(store, docs, recursive=True)
        print(f"indexed {report.files} files, {report.chunks} chunks")
    finally:
        store.close()


def generate(question: str, chunks: list[dict]) -> str:
    """Assemble a grounded answer from retrieved chunks with the local model."""
    context = "\n\n".join(f"[{c['source']}] {c['text']}" for c in chunks)
    prompt = (
        "You answer strictly from the provided context about the "
        "duckdb-vectors library.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}\n"
        "Answer in two or three sentences:"
    )
    llm = _load_llm()
    result = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=256,
    )
    return result["choices"][0]["message"]["content"].strip()


def answer(question: str, db_path: str, top_k: int = TOP_K) -> dict:
    """Retrieve and assemble a grounded answer; return answer plus sources."""
    store = VectorStore(db_path=db_path, backend=BACKEND, read_only=True)
    try:
        hits = store.query(question, top_k=top_k)
        chunks = [{"source": h.chunk.source, "text": h.chunk.text} for h in hits]
    finally:
        store.close()
    generated = generate(question, chunks)
    return {"answer": generated, "sources": [c["source"] for c in chunks]}


def main() -> None:
    parser = argparse.ArgumentParser(description="duckdb-vectors local RAG agent")
    parser.add_argument("question", nargs="?", help="Question to answer.")
    parser.add_argument("--index", help="Folder of markdown to index.")
    parser.add_argument("--db", default="vectors.duckdb", help="DuckDB file.")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument(
        "--warm", action="store_true", help="Pre-fetch the generation model."
    )
    args = parser.parse_args()

    if args.warm:
        _load_llm()  # pull the GGUF into the build cache
        print("model warmed")
        return
    if args.index:
        build_index(args.index, args.db)
        return
    if not args.question:
        parser.error("provide a question, --index, or --warm")

    print(json.dumps(answer(args.question, args.db, args.top_k)))


if __name__ == "__main__":
    main()
