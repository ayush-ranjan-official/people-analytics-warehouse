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
        generate load simulate-day backfill-source check-data fix-source \
        setup-replication ingest check-ingestion \
        dbt-build dbt-nightly freshness storage-report drill validate-lookml eval docs

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

backfill-source: check-env ## Fill a column added to the source after the first load: COLUMN=table.column
	@test -n "$(COLUMN)" || { echo "Set COLUMN=table.column, e.g. COLUMN=ats_requisitions.employment_type"; exit 1; }
	$(RUN) python -m generator.load_to_postgres --backfill $(COLUMN)

fix-source: check-env ## Fix the planted data-quality issues in the source (APPLY=1 commits; default is a dry run)
	$(RUN) python -m generator.fix_source $(if $(APPLY),--apply)

check-data: check-env ## Phase 1 done-check: row counts, FTEs on the last day, planted issues
	$(RUN) python -m generator.verify

# ---------------------------------------------------------------- Phase 2: ingestion
setup-replication: check-env ## Grants + publication for Fivetran (SLOT=1 also creates the replication slot)
	$(RUN) python -m ingestion.setup_replication $(if $(SLOT),--slot)

ingest: check-env ## Copy landing data into BigQuery raw_hris (ALL=1 reloads all; SOURCE=direct bootstraps from the source)
	$(RUN) python -m ingestion.load_to_bigquery $(if $(ALL),--all) $(if $(SOURCE),--source $(SOURCE))

check-ingestion: check-env ## Compare source -> landing -> BigQuery (SOURCE=direct for bootstrap loads)
	$(RUN) python -m ingestion.check_ingestion $(if $(SOURCE),--source $(SOURCE))

# ---------------------------------------------------------------- Phase 3: dbt
DBT := $(RUN) dbt

# dbt packages are installed again only when packages.yml or the lock file changes.
dbt/dbt_packages: dbt/packages.yml dbt/package-lock.yml
	$(DBT) deps $(DBT_DIRS) --quiet
	@touch $@

dbt-build: check-env dbt/dbt_packages ## dbt build: seeds, models, tests; TARGET=prod for production
	$(DBT) build $(DBT_DIRS) --target $(TARGET)

dbt-nightly: check-env dbt/dbt_packages ## Scheduled refresh: run every model (a failing test can't stop a rebuild), then test
	$(DBT) seed $(DBT_DIRS) --target $(TARGET)
	$(DBT) run $(DBT_DIRS) --target $(TARGET)
	$(DBT) test $(DBT_DIRS) --target $(TARGET)

freshness: check-env ## dbt source freshness (warn/error thresholds per table)
	$(DBT) source freshness $(DBT_DIRS) --target $(TARGET)

storage-report: check-env ## Bytes written vs the BigQuery sandbox's 10 GiB lifetime allowance (RECORD=1 saves to the ledger)
	$(RUN) python scripts/storage_report.py $(if $(RECORD),--record)

# ---------------------------------------------------------------- Phase 6: drills
DRILLS := drift_add_column drift_rename_column drift_type_change fail_credentials fail_permissions
drill: check-env ## Break the source on purpose: NAME=<drill>, REVERT=1 undoes it (docs/runbook-*.md)
	@test -n "$(NAME)" && test -n "$(filter $(NAME),$(DRILLS))" || { echo "NAME must be one of: $(DRILLS)"; exit 1; }
	$(RUN) python -m simulations.$(NAME) $(if $(REVERT),--revert)

# ---------------------------------------------------------------- Later phases


validate-lookml: ## Offline LookML checks: parse, field references, mart columns, joins, fanout, dashboards (STRICT=1 fails on warnings)
	$(RUN) python scripts/validate_lookml.py $(if $(STRICT),--strict)

eval: ## (Phase 8) Run the text-to-SQL evaluation
	@echo "Not implemented yet: Phase 8"; exit 1

docs: check-env dbt/dbt_packages ## dbt docs for the analytics dataset, the agent's schema context (eval/), and a 100% coverage check
	$(DBT) docs generate $(DBT_DIRS) --target prod --static
	$(RUN) python scripts/build_schema_context.py
	$(RUN) python scripts/build_schema_context.py --bare
	$(RUN) python scripts/check_doc_coverage.py
