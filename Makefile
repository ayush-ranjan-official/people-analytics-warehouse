# People-Analytics Warehouse task runner.
# Every target loads .env through uv (values are parsed, never executed by the shell)
# and runs inside the project's .venv, even if another virtualenv is active in the shell.

SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE ?= .env
RUN      := VIRTUAL_ENV=$(CURDIR)/.venv uv run --no-project --env-file $(ENV_FILE) --
DBT_DIRS := --project-dir dbt --profiles-dir dbt
TARGET   ?= dev

.PHONY: help setup check check-env check-gcp check-dbt check-postgres setup-gcp \
        generate load simulate-day dbt-build validate-lookml eval docs

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- Phase 0
setup: ## Create .venv (Python 3.12) and install pinned requirements
	uv venv --python 3.12 --allow-existing .venv
	uv pip install --python .venv/bin/python -r requirements.txt

check-env:
	@test -f $(ENV_FILE) || { echo "Missing $(ENV_FILE): copy .env.example to .env and fill it in."; exit 1; }

setup-gcp: check-env ## One-time GCP setup: sandbox project (no billing), service account + key, datasets
	$(RUN) bash scripts/setup_gcp.sh

check-gcp: check-env ## Guard: key file belongs to this project; datasets exist in the right region
	$(RUN) python scripts/check_gcp_identity.py

check-dbt: check-env ## dbt debug against BigQuery
	$(RUN) dbt debug $(DBT_DIRS) --target $(TARGET)

check-postgres: check-env ## Insert and read back a row in Postgres
	$(RUN) python scripts/check_postgres.py

check: check-gcp check-dbt check-postgres ## Phase 0 done-check
	@echo "Phase 0 done-check passed."

# ---------------------------------------------------------------- Later phases
generate: ## (Phase 1) Generate synthetic HR history
	@echo "Not implemented yet: Phase 1"; exit 1

load: ## (Phase 1) Create tables and bulk-load Postgres
	@echo "Not implemented yet: Phase 1"; exit 1

simulate-day: ## (Phase 1) Apply one day of hires, moves and exits
	@echo "Not implemented yet: Phase 1"; exit 1

dbt-build: check-env ## (Phase 3) dbt build; TARGET=prod for production
	@echo "Not implemented yet: Phase 3"; exit 1

validate-lookml: ## (Phase 5) Parse and cross-check LookML files
	@echo "Not implemented yet: Phase 5"; exit 1

eval: ## (Phase 8) Run the text-to-SQL evaluation
	@echo "Not implemented yet: Phase 8"; exit 1

docs: ## (Phase 4) Generate dbt docs and eval/schema_context.md
	@echo "Not implemented yet: Phase 4"; exit 1
