# Getting started

duckdb-vectors indexes a folder of markdown and answers hybrid semantic
searches from a local DuckDB file.

## Installation

Install the package with the local embedding extra for offline use.

## Retries

The system retries transient failures with exponential backoff and gives up
after a bounded number of attempts.
