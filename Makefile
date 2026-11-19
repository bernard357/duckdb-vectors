help:
	@echo "Common commands:"
	@echo "$$ make setup     - Check prerequisites, create the venv, install tooling"
	@echo "$$ make lint      - Run Ruff, Markdown, and Dockerfile lint checks"
	@echo "$$ make test      - Run the pytest suite"
	@echo "$$ make coverage  - Run tests with coverage and write the HTML report"
	@echo "$$ make serve-coverage - Serve the HTML coverage report in a browser"
	@echo "$$ make prune     - Report dead-code candidates with vulture (advisory)"
	@echo "$$ make format    - Format Python code with Ruff"
	@echo "$$ make build     - Build the wheel and sdist with uv"
	@echo "$$ make bench-tagged - Run the tagged retrieval benchmark (downloads data)"
	@echo "$$ make kb-context KB=dir    - Write offline section contexts into a knowledge base"
	@echo "$$ make kb-tags KB=dir       - Derive page-level tags (STEP=discover|tag|apply)"
	@echo "$$ make kb-validation KB=dir - Build a validation set (STEP=local|contextual|...)"
	@echo "$$ make kb-evaluate KB=dir   - Measure tags and contexts on a knowledge base"
	@echo "$$ make kb-batch KB=dir      - Run queued requests as Bedrock batch jobs (STEP=submit|status|fetch)"
	@echo "$$ make clean     - Remove build artifacts and caches"
	@echo "$$ make ping-docker    - End-to-end ping: offline RAG container"
	@echo "$$ make ping-lambda    - End-to-end ping: deploy and invoke on AWS Lambda"
	@echo "$$ make ping-agentcore - End-to-end ping: deploy and invoke on Bedrock AgentCore"
	@echo "$$ make serve-documentation - Serve the VitePress documentation site locally"
	@echo "$$ make build-documentation - Build the VitePress documentation site"
	@echo "$$ make push      - Rebase onto main and push commits to git remote"
	@echo " ... and you have access to git, uv, ruff and pytest directly as well."

# combine lines of each target
.ONESHELL:
# ensure child processes get variables set in this Makefile
.EXPORT_ALL_VARIABLES:

# default embedding backend for the CLI and examples
# "fastembed:<model>" uses fastembed (minimum viable setup); "hf:<repo>" loads
# any Hugging Face ONNX repo; "bedrock:titan" embeds in the cloud; "hashing" is
# a deterministic, dependency-free fixture for tests only
DUCKDB_VECTORS_BACKEND ?= fastembed:minishlab/potion-multilingual-128M
# default DuckDB database path
DUCKDB_VECTORS_DB_PATH ?= vectors.duckdb
# default Python path for imports
PYTHONPATH ?= .
# source locations for static analysis
PYTHON_PATHS := src/ tests/ scripts/
# markdown files for static analysis
MARKDOWN_FILES := README.md CONTRIBUTING.md AGENTS.md $(shell find scripts fixtures/prompts -name '*.md') $(shell find documentation -name '*.md')
# Dockerfiles for static analysis
DOCKERFILES := $(shell find . -name 'Dockerfile' -not -path './.venv/*')

# Serve htmlcov/ over a local HTTP server and open it in the browser. HTTP (not
# file://) is required: coverage's per-file pages touch localStorage on load,
# which browsers forbid on a file:// origin — so a file:// report errors when
# you click into a file instead of showing the annotated source. Picks a free
# port on 127.0.0.1 and runs in the foreground; stop it with Ctrl-C. Kept as a
# define so the Python reads cleanly instead of being escaped into one shell
# line; `make serve-coverage` runs it via `python -c "$$SERVE_COVERAGE_PYSCRIPT"`.
define SERVE_COVERAGE_PYSCRIPT
import functools
import http.server
import webbrowser

handler = functools.partial(
    http.server.SimpleHTTPRequestHandler, directory="htmlcov"
)
server = http.server.HTTPServer(("127.0.0.1", 0), handler)
url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
print(f"Coverage report: {url}  (Ctrl-C to stop)")
webbrowser.open(url)
server.serve_forever()
endef
export SERVE_COVERAGE_PYSCRIPT

setup: check-prerequisites setup-python setup-markdownlint setup-hadolint

check-prerequisites:
	@echo "Checking prerequisites (see CONTRIBUTING.md)..."
	@command -v python3 >/dev/null || { echo "ERROR: python3 not found — install Python 3.12+"; exit 1; }
	@command -v uv >/dev/null || { echo "ERROR: uv not found — see https://docs.astral.sh/uv/"; exit 1; }
	@command -v docker >/dev/null || echo "WARNING: docker not found — needed only for the example container agents"
	@command -v npm >/dev/null || echo "WARNING: npm not found — needed for setup-markdownlint"
	@command -v hadolint >/dev/null || echo "WARNING: hadolint not found — needed for lint-dockerfile; see https://github.com/hadolint/hadolint#install"

setup-python:
	@echo "Creating virtual environment with uv..."
	uv venv --clear $(if $(PYTHON_VERSION),--python $(PYTHON_VERSION),)
	@echo "Installing the package with all extras..."
	uv pip install --python .venv/bin/python -e ".[bedrock,dev]"

setup-markdownlint:
	@command -v markdownlint >/dev/null && markdownlint --version || { \
		echo "Installing markdownlint-cli via npm..."; \
		npm install markdownlint-cli -g || { \
			echo "npm global install failed (often a permissions issue)."; \
			echo "Install markdownlint-cli yourself, e.g. 'npm install -g"; \
			echo "markdownlint-cli' with a user-writable npm prefix. Needed"; \
			echo "only for 'make lint-markdown'."; \
		}; \
	}

setup-hadolint:
	@command -v hadolint >/dev/null && hadolint --version || { \
		echo "hadolint not found. It has no pip/npm package; install it for"; \
		echo "your platform from https://github.com/hadolint/hadolint#install"; \
		echo "(binary release, Docker image 'hadolint/hadolint', or a native"; \
		echo "package). Needed only for 'make lint-dockerfile'."; \
	}

lint: lint-python lint-markdown lint-dockerfile

lint-python:
	@echo "Running Ruff linter..."
	.venv/bin/ruff check ${PYTHON_PATHS}
	@echo "Running Ruff format check..."
	.venv/bin/ruff format --check ${PYTHON_PATHS}
	@echo "Running mypy type check..."
	.venv/bin/mypy

lint-markdown:
	@echo "Validating Markdown files..."
	markdownlint ${MARKDOWN_FILES}

lint-dockerfile:
	@echo "Validating Dockerfiles..."
	hadolint ${DOCKERFILES}

format:
	@echo "Formatting Python code with Ruff..."
	.venv/bin/ruff format ${PYTHON_PATHS}

test:
	@echo "Running tests..."
	.venv/bin/pytest -q -m "not model"

# Tagged retrieval benchmark. Pass options through BENCH_ARGS, for example
# make bench-tagged BENCH_ARGS="--dataset ragmate --conditions baseline"
BENCH_ARGS ?=
bench-tagged:
	@echo "Running the tagged retrieval benchmark..."
	.venv/bin/python -m scripts.benchmark_tagged $(BENCH_ARGS)

# Knowledge-base curation, upstream of indexing (scripts/kb_*.py). KB is the
# folder of Markdown pages, WORK the folder for caches, the validation set and
# results. A language model is called through Amazon Bedrock (eu. profile);
# answers are cached by content. Extra options go through KB_ARGS, for example
# make kb-validation KB=docs STEP=contextual KB_ARGS="--size 300"
KB ?=
WORK ?= tmp/kb/$(notdir $(KB))
STEP ?=
TAGS ?=
KB_ARGS ?=
kb-check:
	@test -n "$(KB)" || (echo "Set KB to the folder of Markdown pages"; exit 1)

kb-context: kb-check
	@echo "Writing section contexts into $(KB)..."
	.venv/bin/python -m scripts.kb_context "$(KB)" --work "$(WORK)" $(value KB_ARGS)

kb-tags: kb-check
	@echo "Deriving page-level tags for $(KB) ($(or $(STEP),discover))..."
	.venv/bin/python -m scripts.kb_tags $(or $(STEP),discover) "$(KB)" --work "$(WORK)" $(value KB_ARGS)

kb-validation: kb-check
	@echo "Building the validation set of $(KB) ($(or $(STEP),local))..."
	.venv/bin/python -m scripts.kb_validation $(or $(STEP),local) "$(KB)" --work "$(WORK)" $(value KB_ARGS)

# Batch inference: run a curation step with KB_ARGS="--batch" to queue its
# requests, then STEP=submit, status and fetch, then the step again.
BATCH_BUCKET ?=
BATCH_ROLE_ARN ?=
kb-batch: kb-check
	@echo "Bedrock batch jobs of $(WORK) ($(or $(STEP),status))..."
	.venv/bin/python -m scripts.kb_batch $(or $(STEP),status) --work "$(WORK)" $(if $(BATCH_BUCKET),--bucket "$(BATCH_BUCKET)") $(if $(BATCH_ROLE_ARN),--role-arn "$(BATCH_ROLE_ARN)")

kb-evaluate: kb-check
	@echo "Evaluating tags and contexts on $(KB)..."
	.venv/bin/python -m scripts.kb_evaluate "$(KB)" --work "$(WORK)" $(if $(TAGS),--tags '$(value TAGS)') $(value KB_ARGS)

coverage:
	@echo "Running tests with coverage..."
	.venv/bin/pytest --cov=src/duckdb_vectors --cov-branch --cov-report=html --cov-report=term-missing
	@echo "HTML report written to htmlcov/ — run 'make serve-coverage' to view it in a browser."

# Serve the generated htmlcov/ report (see SERVE_COVERAGE_PYSCRIPT above).
serve-coverage:
	@test -f htmlcov/index.html || { echo "No htmlcov/index.html — run 'make coverage' first."; exit 1; }
	@.venv/bin/python -c "$$SERVE_COVERAGE_PYSCRIPT"

prune:
	@echo "Scanning for dead-code candidates with vulture (advisory)..."
	@.venv/bin/vulture || true

build:
	@echo "Building wheel and sdist..."
	uv build

# End-to-end ping tests. Each runs a shell snippet from scripts/ (ping_*.sh) that
# builds a search agent from documentation/, runs the fixed query, and pipes
# the result to the shared Python validator. The AWS pings create real,
# billable resources and leave them running; use AWS_PROFILE to select the
# account. The venv is put on PATH so the snippets' `python` is the project's.
PING_ENV := PATH="$(PWD)/.venv/bin:$$PATH"

ping-docker:
	$(PING_ENV) bash scripts/ping_docker.sh

ping-lambda:
	$(PING_ENV) bash scripts/ping_lambda.sh

ping-agentcore:
	$(PING_ENV) bash scripts/ping_agentcore.sh

# Documentation site (VitePress). Node-only targets, namespaced to avoid any
# collision with the Python toolbox above. The site sources live in
# documentation/ and build to documentation/.vitepress/dist.
setup-documentation:
	@echo "Installing documentation site dependencies..."
	npm ci

serve-documentation:
	@echo "Serving the documentation site locally..."
	npm run dev

build-documentation:
	@echo "Building the documentation site..."
	npm run build

preview-documentation:
	@echo "Previewing the built documentation site..."
	npm run preview

rebase:
	git pull --rebase origin main

push: rebase
	git push

clean:
	@echo "Removing documentation site build output and cache..."
	rm -rf documentation/.vitepress/dist documentation/.vitepress/cache
	@echo "Removing build artifacts and caches..."
	rm -rf dist build *.egg-info src/*.egg-info *.out
	rm -rf .ruff_cache .pytest_cache
	@echo "Removing coverage artifacts..."
	rm -rf htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

