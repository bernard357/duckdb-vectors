"""Bedrock AgentCore RAG agent over the duckdb-vectors documentation.

Opens the baked index, retrieves the most relevant chunks for the
incoming question, and assembles a grounded answer with Amazon Nova Pro through
the Bedrock Converse API. The index is embedded with Amazon Titan Embed at
build time; both model calls use the AgentCore runtime's execution-role
credentials.

AgentCore invokes the ``@app.entrypoint`` function with the request payload and
returns its result. Payload: {"question": "..."} (or {"prompt": "..."}).
"""

import os

import boto3
from bedrock_agentcore import BedrockAgentCoreApp

from duckdb_vectors import VectorStore

REGION = os.environ.get("AWS_REGION", "us-east-1")
RAG_MODEL_ID = os.environ.get("RAG_MODEL_ID", "us.amazon.nova-pro-v1:0")
DB_PATH = os.environ.get("DUCKDB_VECTORS_DB_PATH", "vectors.duckdb")
TOP_K = int(os.environ.get("PING_TOP_K", "5"))

app = BedrockAgentCoreApp()
_store = VectorStore(db_path=DB_PATH, backend="bedrock:titan")
_bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def _generate(question: str, chunks: list[dict]) -> str:
    context = "\n\n".join(f"[{c['source']}] {c['text']}" for c in chunks)
    prompt = (
        "You answer strictly from the provided context about the "
        "duckdb-vectors library.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}\n"
        "Answer in two or three sentences:"
    )
    response = _bedrock.converse(
        modelId=RAG_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 256, "temperature": 0.0},
    )
    return response["output"]["message"]["content"][0]["text"].strip()


@app.entrypoint
def invoke(payload):
    question = payload.get("question") or payload.get("prompt") or ""
    hits = _store.query(question, top_k=TOP_K)
    chunks = [{"source": h.chunk.source, "text": h.chunk.text} for h in hits]
    answer = _generate(question, chunks)
    return {"answer": answer, "sources": [c["source"] for c in chunks]}


if __name__ == "__main__":
    app.run()
