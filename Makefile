# People-Analytics Warehouse task runner.
# Targets load .env through uv when it exists (values are parsed, never executed by the shell);
# in CI the same settings come from the job's environment. Everything runs in the project's
# .venv, even if another virtualenv is active in the shell.

SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE ?= .env
ENV_FLAG := $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE))
# The env-file check runs first: on a malformed line uv would quote the line, i.e. print a secret.
RUN      := python3 scripts/check_env_file.py $(ENV_FILE) && VIRTUAL_ENV=$(CURDIR)/.venv uv run --no-project $(ENV_FLAG) --
DBT_DIRS := --project-dir dbt --profiles-dir dbt
TARGET   ?= dev

.PHONY: help setup check check-env check-gcp check-dbt check-postgres setup-gcp \
        generate load simulate-day check-data \
        setup-replication ingest check-ingestion dbt-build validate-lookml eval docs

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- Phase 0
setup: ## Create .venv (Python 3.12) and install pinned requirements
	uv venv --python 3.12 --allow-existing .venv
	uv pip install --python .venv/bin/python -r requirements.txt

check-env:
	@test -f $(ENV_FILE) || test -n "$$CI" || { echo "Missing $(ENV_FILE): copy .env.example to .env and fill it in."; exit 1; }

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

# ---------------------------------------------------------------- Phase 1: source data
generate: ## Generate the synthetic HRIS + ATS history into generator/output
	$(RUN) python -m generator.generate

load: check-env ## First load into the source Postgres (refuses once Fivetran replicates it)
	$(RUN) python -m generator.load_to_postgres

simulate-day: check-env ## Apply the next business day of hires, moves and exits (DAYS=n for more)
	$(RUN) python -m generator.load_to_postgres --simulate-day --days $(or $(DAYS),1)

check-data: check-env ## Phase 1 done-check: row counts, FTEs on the last day, planted issues
	$(RUN) python -m generator.verify

# ---------------------------------------------------------------- Phase 2: ingestion
setup-replication: check-env ## Grants + publication for Fivetran (SLOT=1 also creates the replication slot)
	$(RUN) python -m ingestion.setup_replication $(if $(SLOT),--slot)

ingest: check-env ## Copy landing data into BigQuery raw_hris (ALL=1 reloads all; SOURCE=direct bootstraps from the source)
	$(RUN) python -m ingestion.load_to_bigquery $(if $(ALL),--all) $(if $(SOURCE),--source $(SOURCE))

check-ingestion: check-env ## Compare source -> landing -> BigQuery (SOURCE=direct for bootstrap loads)
	$(RUN) python -m ingestion.check_ingestion $(if $(SOURCE),--source $(SOURCE))

# ---------------------------------------------------------------- Later phases

dbt-build: check-env ## (Phase 3) dbt build; TARGET=prod for production
	@echo "Not implemented yet: Phase 3"; exit 1

validate-lookml: ## (Phase 5) Parse and cross-check LookML files
	@echo "Not implemented yet: Phase 5"; exit 1

eval: ## (Phase 8) Run the text-to-SQL evaluation
	@echo "Not implemented yet: Phase 8"; exit 1

docs: ## (Phase 4) Generate dbt docs and eval/schema_context.md
	@echo "Not implemented yet: Phase 4"; exit 1
