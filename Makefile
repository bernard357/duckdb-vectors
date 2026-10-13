help:
	@echo "Common commands:"
	@echo "# make setup     - Create the virtual environment and install dependencies"
	@echo "$$ make lint      - Run Ruff lint and format checks"
	@echo "$$ make test      - Run the pytest suite"
	@echo "$$ make format    - Format Python code with Ruff"
	@echo "$$ make build     - Build the wheel and sdist with uv"
	@echo "$$ make clean     - Remove build artifacts and caches"
	@echo "$$ make push      - Rebase onto main and push commits to git remote"
	@echo " ... and you have access to git, uv, ruff and pytest directly as well."

# combine lines of each target
.ONESHELL:
# ensure child processes get variables set in this Makefile
.EXPORT_ALL_VARIABLES:

# default embedding backend for the CLI and examples
# "local" uses fastembed (minimum viable setup); "bedrock:titan" embeds in the
# cloud; "hashing" is a deterministic, dependency-free fixture for tests only
DUCKDB_VECTORS_BACKEND ?= local
# default DuckDB database path
DUCKDB_VECTORS_DB_PATH ?= vectors.duckdb
# default Python path for imports
PYTHONPATH ?= .
# source locations for static analysis
PYTHON_PATHS := src/ tests/ examples/

setup:
	@echo "Creating virtual environment with uv..."
	uv venv
	@echo "Installing the package with all extras..."
	uv pip install -e ".[local,bedrock,cli,dev]"

lint:
	@echo "Running Ruff linter..."
	.venv/bin/ruff check ${PYTHON_PATHS}
	@echo "Running Ruff format check..."
	.venv/bin/ruff format --check ${PYTHON_PATHS}

format:
	@echo "Formatting Python code with Ruff..."
	.venv/bin/ruff format ${PYTHON_PATHS}

test:
	@echo "Running tests..."
	.venv/bin/pytest -q

build:
	@echo "Building wheel and sdist..."
	uv build

clean:
	@echo "Removing build artifacts and caches..."
	rm -rf dist build *.egg-info src/*.egg-info
	rm -rf .ruff_cache .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

rebase:
	git pull --rebase origin main

push: rebase
	git push
