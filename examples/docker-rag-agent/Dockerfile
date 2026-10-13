# A fully offline RAG agent over the duckdb-vectors documentation.
#
# The build stage installs the local embedding backend (fastembed) and the
# local generation model (Qwen2.5-0.5B GGUF via llama-cpp-python), indexes the
# documentation, and warms the model into the cache. The runtime stage carries
# the agent, the frozen index, the fastembed model, and the GGUF cache, so the
# container retrieves and assembles a grounded answer with no network.
#
# Build from the repository root:
#   docker build -f examples/docker-rag-agent/Dockerfile -t duckdb-vectors-rag .
# Run a query (prints JSON {answer, sources}):
#   docker run --rm duckdb-vectors-rag "how is a search answered?"

FROM python:3.12-slim AS builder
WORKDIR /build
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
ENV HF_HOME=/opt/hf_cache
# llama-cpp-python compiles native code; provide a C/C++ toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake && rm -rf /var/lib/apt/lists/*
COPY examples/docker-rag-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# duckdb-vectors is not yet on PyPI; install it from the locally built wheel.
# Build it first with `uv build`, then build this image from the repo root.
COPY dist/ ./dist/
RUN pip install --no-cache-dir "$(echo ./dist/*.whl)[local]"
COPY examples/docker-rag-agent/agent.py .
COPY documentation/ ./documentation/
RUN python agent.py --index ./documentation --db /build/vectors.duckdb
RUN python agent.py --warm

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
ENV HF_HOME=/opt/hf_cache
# llama-cpp-python compiles native code at install time; the toolchain is
# needed in the runtime image too (a test container, not a slim production one).
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake && rm -rf /var/lib/apt/lists/*
COPY examples/docker-rag-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dist/ ./dist/
RUN pip install --no-cache-dir "$(echo ./dist/*.whl)[local]" && rm -rf ./dist
COPY examples/docker-rag-agent/agent.py .
COPY --from=builder /build/vectors.duckdb /app/vectors.duckdb
COPY --from=builder /opt/fastembed_cache /opt/fastembed_cache
COPY --from=builder /opt/hf_cache /opt/hf_cache
ENV DUCKDB_VECTORS_DB_PATH=/app/vectors.duckdb
ENV DUCKDB_VECTORS_READ_ONLY=true
ENTRYPOINT ["python", "agent.py", "--db", "/app/vectors.duckdb"]
CMD ["how is a search answered?"]
