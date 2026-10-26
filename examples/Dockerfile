# Build-time indexing: bake a frozen vector index into the image.
#
# Stage 1 indexes a folder of markdown with the fastembed embedding backend and
# produces vectors.duckdb. Stage 2 copies only the runtime code and the frozen
# index, so the container opens it and answers in tens of ms.

FROM python:3.12-slim AS builder
WORKDIR /build
COPY . /build
RUN pip install --no-cache-dir "."
# docs/ is your markdown corpus; adjust the path to your project.
RUN duckdb-vectors index ./docs --db /build/vectors.duckdb \
    --backend fastembed:minishlab/potion-multilingual-128M

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=builder /build/vectors.duckdb /app/vectors.duckdb
RUN pip install --no-cache-dir "duckdb-vectors"
ENV DUCKDB_VECTORS_DB_PATH=/app/vectors.duckdb
ENV DUCKDB_VECTORS_BACKEND=fastembed:minishlab/potion-multilingual-128M
CMD ["duckdb-vectors", "search", "how does the system handle retries?"]
