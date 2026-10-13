# Architecture

One DuckDB file is one index. The store keeps chunks, metadata, and a
fixed-size embedding column, and fuses vector and BM25 rankings.

## Read-only mode

A frozen index baked into a container image opens read-only and never writes,
which is the fastest path for serving.
