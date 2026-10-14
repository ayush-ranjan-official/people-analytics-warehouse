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

## D3. Source system: free Neon Postgres, not Google Sheets
*2026-10-14*

**Decision.** The fake HRIS/ATS is a Postgres 18 database on Neon's free plan (no card; Fivetran supports
PG 18), in AWS Singapore, the closest Neon region to Mumbai. All 8 tables live in a Postgres schema
called `hris`.

**Why not Google Sheets.**
- The pipeline has to show how schema drift and sync failures from a source system are handled.
  A database connection with change data capture makes all five Phase 6 drills real:
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
- **Fivetran** gets a dedicated login on the source (replication only) and another on the landing database.
  The Phase 6 "rotate the credential" and "stop one table" drills then break Fivetran without breaking
  anything else.
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
| `dbt snapshot` (MERGE) for SCD Type 2 | A table model that rebuilds itself from its own previous version plus the new changes (`CREATE OR REPLACE TABLE … AS SELECT … FROM {{ this }} …`), closing old versions and opening new ones. No DML. |
| Incremental `fct_headcount_daily` (MERGE) | Full rebuild each run. The data is small, so this is fine. The incremental design, including the lookback window for back-dated terminations, is documented for production. |
| Partition by `snapshot_date` | **Integer-range** partition on `snapshot_month` (yyyymm) plus clustering by department and location. Date partitions older than 60 days are dropped by the sandbox. |
| Tables kept forever | A nightly `dbt build` (GitHub Actions) rebuilds tables before the 60-day expiry. Tables don't depend on it to stay correct, only to stay present. |
| Storage is cheap | **Light on storage:** staging and intermediate models are views, only marts are tables, and storage use is tracked against the 10 GiB lifetime cap. |

Each row is a deliberate trade-off between what the platform allows and what the model needs. With
billing enabled, the left-hand column is the design to use.

## D7. Semantic layer and BI: LookML in the repo, Data Studio for live dashboards
*2026-10-14*

Looker has no free self-serve tier for your own data. So:
- **LookML:** the views, explores and dashboard are written as LookML in `lookml/` and validated offline
  (parse, field references, every measure documented).
- **Looker IDE practice:** Google Cloud Skills Boost LookML labs that are marked "No cost". They run on
  Google's sample data, not ours.
- **Live dashboards:** Data Studio (Looker Studio's name since 2026), reading the BigQuery marts. It is
  free. Connecting it to a sandbox dataset is confirmed hands-on in Phase 5.

## D8. The text-to-SQL eval calls Claude through the Claude Code CLI
*2026-10-14*

The eval (Phase 8) sends each question to Claude in a fresh, tool-less session through the Claude Code
CLI, which runs on a subscription instead of per-call API billing. `ANTHROPIC_API_KEY` is left unset:
if it were present, the CLI would switch to API billing.
