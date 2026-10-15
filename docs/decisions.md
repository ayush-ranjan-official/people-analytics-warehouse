# Architecture decisions

A short log of the decisions behind this project: what was chosen, what was rejected, and why.
Each entry is dated. When a later decision replaces one, the old entry says so instead of being deleted.

Constraints that drive most of these:
- **Stack:** BigQuery, Fivetran, dbt and LookML/Looker, with Data Studio for live dashboards.
- **₹0 and no payment card anywhere.** Scale isn't needed: the data is a few hundred thousand rows at most.
- **All data is synthetic.**

Verified platform limits behind these decisions are in [platform-notes.md](platform-notes.md).

---

## D1. BigQuery sandbox: a project with no billing account
*2026-10-14 · replaces an earlier draft that used a Free Trial billing account*

**Decision.** BigQuery runs in project `people-analytics-demo-2610`, which has **no billing account**:
the BigQuery sandbox. A project with no billing account cannot be charged, so the ₹0 guarantee is
structural rather than a matter of alerts. `scripts/setup_gcp.sh` checks this and stops if billing is
ever turned on.

**Rejected:** the Google Cloud Free Trial (needs a payment card; Indian sign-ups are sometimes asked for
a ~₹1,000 prepayment) and a paid billing account (needs a card; nothing truly caps spend).

**What the sandbox costs us** (each limit tested in this project, see platform-notes.md):
- **No DML**, so no MERGE/INSERT. dbt snapshots and incremental models can't run. → D6
- **Date partitions older than 60 days are dropped immediately.** → D6
- **Tables expire 60 days after they're created.** → nightly rebuild (D6)
- **10 GiB of storage for the project's lifetime**, and deleting data doesn't give it back. → D6
- **Fivetran won't write to a sandbox project.** → D2

## D2. Fivetran lands in a Neon Postgres "landing" database; a batch loader moves it to BigQuery
*2026-10-14*

**Decision.** The data flows like this:
1. Fivetran (Free plan) syncs the HRIS source into a second Neon Postgres database, the landing zone,
   using Fivetran's generic PostgreSQL destination.
2. `load_to_bigquery.py` copies each landed table, including Fivetran's `_fivetran_synced` and
   `_fivetran_deleted` columns, into BigQuery dataset `raw_hris` with batch load jobs. Batch loads are
   free and allowed in the sandbox.

**Why.** Fivetran is part of the target stack, but it refuses sandbox destinations (D1). Landing in Postgres
keeps everything Fivetran does real: change data capture, schema-drift handling, sync failures,
alerts, soft deletes. The dbt models read `raw_hris` exactly as if Fivetran had written it, so in
production the extra hop goes away by pointing Fivetran's destination at BigQuery. No model changes.

**Trade-off.** One extra step to run and monitor. Freshness is measured on `_fivetran_synced`, so a
stuck loader shows up as stale data, just like a stuck Fivetran sync would.

**Bootstrap mode.** `make ingest SOURCE=direct` copies the source tables straight into `raw_hris`,
adding the two Fivetran columns itself (nothing deleted; synced = load time). It let the warehouse be
built and tested before the Fivetran connection went live, and is kept for rebuilding from scratch.
Every BigQuery table is
labelled with its origin (`load_source = direct` or `fivetran`), and switching back to Fivetran
reloads every table.

## D3. Source system: free Neon Postgres, not Google Sheets
*2026-10-14*

**Decision.** The fake HRIS/ATS is a Postgres 18 database on Neon's free plan (no card; Fivetran supports
PG 18), in AWS Singapore, the closest Neon region to Mumbai. All 8 tables live in a Postgres schema
called `hris`.

**Why not Google Sheets.**
- The pipeline has to show how schema drift and sync failures from a source system are handled.
  A database connection with change data capture makes all five drills real:
  - add a column
  - rename a column
  - change a column's type
  - rotate the credential Fivetran uses
  - stop one table from replicating
- Sheets has no column types (so a type change can't be simulated) and identifies rows by row number.
  It would also need one Fivetran connection per table.

## D4. Identities: one service account for dbt and scripts; Fivetran gets its own Postgres logins
*2026-10-14*

- **dbt, the loader and the checks** use BigQuery service account `dbt-bigquery`, with BigQuery Data Editor
  and Job User only. Its key lives outside the repo (permissions 600). The dbt profile authenticates only
  with that key, never with a personal Google login.
- **Fivetran** gets a dedicated `fivetran` login on the source and another on the landing database, so
  the "rotate the credential" and "stop one table" drills break Fivetran without breaking anything
  else. They are separate logins, but not least-privilege: on Neon, only roles created in the console can
  have REPLICATION, and those roles are members of `neon_superuser` (which can read every table). That's
  why the "stop one table" drill removes the table from the replication publication instead of revoking
  SELECT. In production the source login would be a replication-only role.
- **gcloud setup** runs as the personal account, in a gcloud configuration used only by this repo.

## D5. dbt Core 1.12 + dbt-bigquery, not the new `dbt` package
*2026-10-14*

`pip install dbt` now installs dbt v2, a Rust engine under a proprietary licence. `dbt-core` 1.12 is
Apache-2.0 and supported until July 2027. Top-level dependencies are pinned exactly in
`requirements.txt`. Faker is pinned too, because a fixed seed only reproduces the same synthetic data
on the same Faker version.

## D6. dbt models are designed around the sandbox limits
*2026-10-14*

| Normal BigQuery design | Sandbox-compatible design used here |
|---|---|
| `dbt snapshot` (MERGE) for SCD Type 2 | `snap_employees`, a table model that rebuilds itself from its own previous version plus the new changes (`CREATE OR REPLACE TABLE … AS SELECT … FROM {{ this }} …`), closing old versions and opening new ones. No DML. It bootstraps on its first run and is idempotent (an unchanged rebuild is identical). Records deleted at the source are closed (dbt's `hard_deletes='invalidate'`), so it refuses to run if staging has lost more than 10% of the open versions, or has a duplicated `employee_id`. A version never starts at or before the previous change, even if `updated_at` doesn't move. A newly tracked column is filled in on the open versions rather than re-versioning everyone. `valid_from` is when the HRIS record changed (system time); business dates live in job history. |
| Snapshots are recoverable | The table is the only copy of its history, so before each prod build a pre-hook copies it to `snap_employees_prev`, but only if it passes its own checks (one current version per employee, unique keys, valid ranges). Restore with `create or replace table analytics.snap_employees as select * from analytics.snap_employees_prev`; time travel also reaches back 7 days. |
| Incremental `fct_headcount_daily` (MERGE), partitioned by date | A **view** that expands `dim_employee` (a few thousand rows) over the calendar at query time. As a table it was ~32 MiB rewritten on every build, the largest single write against the lifetime storage cap, and rebuilding it less often left it days behind the other marts. As a view it's always consistent with them, costs no storage, and each query reads about a megabyte. With billing, it would be an incremental table partitioned by `snapshot_date`, with a lookback window for back-dated exits. |
| Partition by date | Nothing is date-partitioned: the sandbox drops date partitions older than 60 days. Integer-range partitioning on a yyyymm key was tested and works if a large table ever needs it. |
| Tables kept forever | A nightly job (GitHub Actions) rebuilds every table before the 60-day expiry: `dbt run` then `dbt test`. A failing test can't stop a rebuild, which also matters because `snap_employees` only records changes on the days it runs. |
| Storage is cheap | **Light on storage:** staging, intermediate and the headcount fact are views, the other marts are tables, surrogate keys are INT64 (`farm_fingerprint`) rather than 32-character hashes, and unchanged raw tables aren't reloaded. `make storage-report` counts bytes **written** (what the sandbox's lifetime limit counts), from the job history, into a small ledger table, and projects when 5 GiB and 10 GiB would be reached. |

Each row is a deliberate trade-off between what the platform allows and what the model needs. With
billing enabled, the left-hand column is the design to use.

## D7. Semantic layer and BI: LookML in the repo, Data Studio for live dashboards
*2026-10-14*

Looker has no free tier for your own data. So:
- **LookML:** the views, explores and dashboard are written as LookML in `lookml/` and validated offline
  (parse, field references, every measure documented).
- **Live dashboards:** Data Studio (Looker Studio's name since 2026), reading three dbt reporting views
  over the marts (`analytics.rpt_*`, see [dashboards.md](dashboards.md)). It is free. The views hold
  additive counts on a dense daily grid, so rates are calculated fields that stay right under any
  filter.

## D8. The text-to-SQL eval calls Claude through the Claude Code CLI
*2026-10-14*

The eval sends each question to Claude in a fresh, tool-less session through the Claude Code
CLI, which runs on a subscription instead of per-call API billing. `ANTHROPIC_API_KEY` is left unset:
if it were present, the CLI would switch to API billing.

## D9. The source data is a deterministic simulation that can be cut at any date
*2026-10-14*

**Decision.** `generator/` simulates the company day by day. Every random draw is seeded by what it
is about (a day, a requisition, a person), so simulating further never changes the past. The same
code produces the initial history (`make generate`, `make load`) and each new day of changes
(`make simulate-day`): it simulates to the next date, compares with the day before, and writes only
the differences.

**Why.** The pipeline needs a source that keeps changing like a live HRIS, so incremental syncs,
freshness checks and drift drills are real. It must also be reproducible: the CSVs carry SHA-256
checksums, and `simulate-day` refuses to run if the config, numpy or Faker version no longer matches
what was loaded, rather than silently mixing two histories.

**Source-system realism.**
- Tables have primary keys (needed for replication) but no foreign keys or check constraints, like
  many real source systems.
- Known data-quality problems were planted deliberately (`generator/PLANTED_ISSUES.md`) so the
  warehouse tests catch real issues at the source; after the fix, planting was switched off
  (`planted_issues.enabled: false`).
