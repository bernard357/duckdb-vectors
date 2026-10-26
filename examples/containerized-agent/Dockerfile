# A tiny answering agent over the duckdb-vectors documentation.
#
# Stage 1 installs the fastembed backend, downloads its ONNX model, and
# indexes the project's documentation/ folder into a frozen DuckDB file. Stage 2
# copies only the agent code, the frozen index, and the model cache, so the
# container answers fully offline with no build tooling.
#
# Build from the repository root:
#   docker build -f examples/containerized-agent/Dockerfile -t duckdb-vectors-agent .
# Run a query:
#   docker run --rm duckdb-vectors-agent "how is a search answered?"
# Chat interactively:
#   docker run --rm -it duckdb-vectors-agent --chat

FROM python:3.12-slim AS builder
WORKDIR /build
# Pin the fastembed model cache to a known path so it can be copied forward.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
COPY examples/containerized-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY examples/containerized-agent/agent.py .
COPY documentation/ ./documentation/
RUN python agent.py --index ./documentation --db /build/vectors.duckdb

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
COPY examples/containerized-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY examples/containerized-agent/agent.py .
COPY --from=builder /build/vectors.duckdb /app/vectors.duckdb
# Carry the downloaded fastembed model so the container needs no network.
COPY --from=builder /opt/fastembed_cache /opt/fastembed_cache
ENV DUCKDB_VECTORS_DB_PATH=/app/vectors.duckdb
ENTRYPOINT ["python", "agent.py", "--db", "/app/vectors.duckdb"]
CMD ["what is duckdb-vectors?"]
