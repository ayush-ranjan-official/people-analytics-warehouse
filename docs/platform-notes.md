# Platform notes

Facts about the free tiers this project runs on, checked on **2026-10-14**. Free tiers change, so each
note says where it came from: **tested** here, from the official **docs**, or **reported** in forums
(treat these with caution). [decisions.md](decisions.md) explains what was decided because of them.

## BigQuery sandbox (project `people-analytics-demo-2610`, no billing account)

| Behaviour | Source |
|---|---|
| `CREATE OR REPLACE TABLE … AS SELECT`, including partitioned and clustered tables, works | tested |
| A table can be rebuilt from its own previous version (`… AS SELECT … FROM <same table>`) | tested |
| Views, scripting (`DECLARE`/`SET`, temp tables) and batch load jobs (`bq load --replace`) work | tested |
| Copy jobs from a partition decorator (`table$20260101`) work | tested |
| `MERGE` and `INSERT` fail: "DML queries are not allowed in the free tier" | tested |
| New datasets get a 60-day default table and partition expiration | tested (`defaultTableExpirationMs = 5184000000`) |
| **Date-partitioned tables drop every partition older than 60 days on creation.** 1,000 days loaded, 43 kept. | tested |
| Setting `partition_expiration_days` above 60 is rejected: "must be less than 60 days while in sandbox mode" | tested |
| Integer-range partitioning (e.g. on yyyymm) and unpartitioned clustered tables keep all historical rows | tested |
| Views in a sandbox dataset expire after 60 days too, so `dbt run` must recreate them | tested |
| `INFORMATION_SCHEMA.TABLE_STORAGE` needs a project option an admin must enable; table metadata (`num_bytes`) gives the same sizes with no extra permission | tested |
| dbt table models, seeds (load jobs), tests, `persist_docs`, source freshness and `dbt show` all work in the sandbox | tested |
| Storage: "a lifetime limit of 10 GiB … not refunded upon data deletion" | [docs](https://docs.cloud.google.com/bigquery/docs/sandbox) |
| Query processing: 1 TiB per month | docs |
| Not supported: streaming, DML, Data Transfer Service | docs |
| Fivetran: "Fivetran doesn't support BigQuery sandbox accounts"; its setup test checks that billing is enabled | [Fivetran docs](https://fivetran.com/docs/destinations/bigquery/setup-guide) |
| Parquet batch loads keep types exactly: DATE (including 9999-12-31), NUMERIC, BOOL, TIMESTAMP | tested |
| A table's expiry can be set up to 59 days ahead with a metadata update after loading | tested |
| A service account can list its own jobs (`client.list_jobs`). Load jobs report `outputBytes`; CREATE TABLE AS SELECT jobs report rows written in the query plan's output stage | tested |

Why we don't use billing at all: the Free Trial needs a payment card, and Indian sign-ups are
**reported** to be asked for a ~₹1,000 prepayment that's refunded only when the account is closed.
Spend-cap budgets don't cover BigQuery, and budget alerts never stop spend (**docs**).

## Neon (free plan, no card)

- Limits: 100 CU-hours and 5 GB egress per project per month, 1 GB storage per project (20 GB per
  account). Running out suspends the compute until next month; it doesn't bill. Logical-replication
  traffic counts as egress. **docs**
- Roles belong to one branch. Roles created in the console are members of `neon_superuser` (REPLICATION,
  `pg_read_all_data`, CREATEROLE, ...); roles created with SQL can never get REPLICATION. The owner role
  can't change a console role's settings, but a role can change its own. **docs, tested**
- Neon sets `wal_sender_timeout = 10s`; Fivetran warns on anything from 1 ms to 30 s. Fix applied:
  `alter role fivetran set wal_sender_timeout = '5min'`, run as `fivetran` itself. **docs, tested**
- Compute scales to zero after 5 minutes idle, and that can't be turned off on Free. **docs**
- Use the **direct** host for replication. The `-pooler` host breaks logical replication.
  `make check-postgres` rejects it. **docs**
- Enabling logical replication is per project and **can't be undone**. **docs**
- Neon **removes a replication slot after ~40 hours** without progress. If Fivetran is paused, or a
  failure drill is left broken, longer than that, the slot has to be recreated and Fivetran must
  re-sync everything: a new slot with the same name can't resume. A Neon branch restore also deletes
  all slots. **docs**
- Roles made in the Neon Console, CLI or API inherit `pg_read_all_data`, so `REVOKE SELECT` on one
  table does nothing for them. For the "stop one table" drill, remove the table from the publication
  instead (`ALTER PUBLICATION … DROP TABLE …`). **docs**
- No Mumbai region. AWS Singapore (`aws-ap-southeast-1`) is the closest. **docs**

## Fivetran (Free plan, no card)

- 500,000 monthly active rows, 5,000 model runs, syncs as often as every 15 minutes. No card needed.
  Going over 500k twice freezes the account; it doesn't bill. **docs**
- A new account starts on a **14-day trial**, which begins with the first sync. When it ends you must
  **choose the Free plan yourself** on the Billing page, or connections stay paused. **docs**
- Neon is supported as a **source** through the generic PostgreSQL connector, with logical replication
  (`pgoutput`) or query-based (xmin) sync. Teleport is retired. **docs**
- **Destination:** Fivetran's generic PostgreSQL destination. Neon isn't named as a destination in
  Fivetran's docs, but it works: the initial sync on 2026-10-15 loaded all 8 tables (143,996 rows) in
  47 s, and every row count matched the source. Text columns land as `character varying`; dates,
  timestamps, numerics and booleans keep their source types. Both text types load into BigQuery as
  STRING. **tested**
- The setup test flags Neon's `wal_buffers` (7,360 kB) as outside best practice. It's harmless: the
  value is Postgres's own auto setting (1/32 of `shared_buffers`, which is what Fivetran recommends),
  and Neon doesn't let you change it. **tested**
- Sync frequency here is every 6 hours, so each night's simulated day reaches the landing database
  well before the next nightly load. **tested**
- Schema drift: an added column flows in; a **renamed column arrives as a new column, and the old one
  stays** (old rows keep it, new rows go NULL); `ALTER COLUMN TYPE` is supported only with logical
  replication. **docs**
- Choose **"Fivetran naming"** for destination names. "Source naming" ignores the schema prefix. **docs**
- A **Fivetran Platform** connection is added to every destination automatically. It writes logs to
  schema `fivetran_metadata` in the landing database once a day (free MAR). The loader ignores it. **docs**
- Failure emails: a connection error is emailed after the **second** consecutive failed sync, a
  destination error after the first. Turn emails on at Notifications > Connections. **docs**
- Processing location for this project: Singapore, on GCP (the only provider on the Free plan). **docs**

## dbt

- dbt 1.12 automatically loads a `.env` file, searching upward through parent folders. **docs**
- `pip install dbt` installs dbt v2 (proprietary licence). We pin `dbt-core` 1.12.5. **docs**
- In 1.12, generic test arguments go under `arguments:`. Use `metaplane/dbt_expectations`; the
  `calogica` original is unmaintained. **docs**
- In `dbt build`, a failing test on a **source** skips every model downstream of it. `dbt run` then
  `dbt test` doesn't. **tested**
- Changing a model from table to view needs `--full-refresh` on that model (or dropping the table)
  once; dbt won't replace a table with a view on its own. **tested**

## Other

- **Data Studio** is Looker Studio's name since April 2026. It's free. Every chart view runs BigQuery
  queries, so don't share dashboards widely. **docs**
- **GitHub Actions** is free for public repos. Scheduled workflows are switched off after 60 days with
  no repository activity. **docs**
- **Google Cloud Skills Boost** LookML labs GSP890 and GSP891 showed "No cost" on 2026-10-14. They give a
  temporary Looker IDE on Google's sample data. **docs**
