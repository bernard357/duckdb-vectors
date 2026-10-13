#!/usr/bin/env bash
# End-to-end ping: build the offline RAG container and validate a search.
#
# Builds the docker-rag-agent image from documentation/, runs the fixed query
# in a container, and pipes the agent's JSON output to the shared validator.
# Fully local and offline. Run from the repository root (via `make ping-docker`).
set -euo pipefail

IMAGE_TAG="duckdb-vectors-rag-ping"
QUERY="how is a search answered?"

echo "[docker] building ${IMAGE_TAG} (bakes the embedding + generation models)..."
docker build -f examples/docker-rag-agent/Dockerfile -t "${IMAGE_TAG}" .

echo "[docker] running the query in a container..."
docker run --rm "${IMAGE_TAG}" "${QUERY}" | python scripts/ping_validate.py docker
