# A tiny answering agent over the duckdb-vectors documentation.
#
# Stage 1 indexes the project's documentation/ folder into a frozen DuckDB
# file. Stage 2 copies only the agent code and that frozen index, so the
# container answers questions read-only with no build tooling in the image.
#
# Build from the repository root:
#   docker build -f examples/docker-agent/Dockerfile -t duckdb-vectors-agent .
# Run a query:
#   docker run --rm duckdb-vectors-agent "how is a search answered?"

FROM python:3.12-slim AS builder
WORKDIR /build
COPY examples/docker-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY examples/docker-agent/agent.py .
COPY documentation/ ./documentation/
RUN python agent.py --index ./documentation --db /build/vectors.duckdb

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY examples/docker-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY examples/docker-agent/agent.py .
COPY --from=builder /build/vectors.duckdb /app/vectors.duckdb
ENV DUCKDB_VECTORS_DB_PATH=/app/vectors.duckdb
ENV DUCKDB_VECTORS_READ_ONLY=true
ENTRYPOINT ["python", "agent.py", "--db", "/app/vectors.duckdb"]
CMD ["what is duckdb-vectors?"]
