# Runbook: schema drift in the HR source

For when a source table's columns change under the warehouse: a column is **added**, **renamed** or
**changes type**. Each case was rehearsed with a drill script in [simulations/](../simulations/), and
the outputs below come from those runs. The [data contract](data-contract-hris.md) lists what the
warehouse depends on.

## How it's detected

- **Schema contract tests** on every source table (`schema_contract` in
  [_sources.yml](../dbt/models/staging/_sources.yml)). The breaking check fails when a contract column
  is missing or has another type. The additive check warns about a column that isn't in the contract.
  Both print the column and the two types.
- **Value tests** in staging and the marts: `accepted_values`, `not_null` on `dim_employee.location`
  and `job_level`, and a warn-level format check on the landed `job_level`.
- **Where it shows up:** `make dbt-build` output. In the nightly GitHub Actions run, the job turns red,
  the run summary lists the failing tests, and GitHub emails whoever last edited the workflow's
  schedule (for a scheduled run, if their notifications are on).

`make dbt-build` and the nightly job react differently to a breaking change:

- **`make dbt-build`** runs tests in DAG order. A failed source test **skips every model downstream**,
  so the mart tables keep the last good build.
- **The nightly job** runs every model first and then tests them (`make dbt-nightly`). It has to:
  `snap_employees` only records a change if it runs on the day the change is visible, and sandbox
  tables must be rewritten before they expire. A model that can't compile still fails and its
  children are skipped. A model that compiles is rebuilt, and the failing test then turns the run red.

## What each change looks like

| Change | With Fivetran | With the bootstrap load (tested) | Effect on dashboards |
| --- | --- | --- | --- |
| **Column added** (`work_mode` on `hris_employees`) | Fivetran adds the column to the landing table; the next load copies it | additive contract **WARN**: `hris_employees.work_mode: contract says NOT IN CONTRACT, table has STRING`; everything else passes | none |
| **Column renamed** (`location` → `work_location` on `hris_job_history`) | Fivetran adds `work_location` and keeps `location`, which is NULL on every row written after the rename | `location` is gone: breaking contract **FAIL** (`contract says STRING, table has MISSING`) and additive **WARN** (`work_location`); `dbt build` skips staging and everything built from it | Fivetran: people who change jobs show a blank location, and `not_null` on `dim_employee.location` **FAILS**. Bootstrap: marts keep the previous build |
| **Type changed** (`job_level` text `L4` → integer `4`) | Fivetran only widens types, so the landed column stays text and new rows arrive as `'4'` | breaking contract **FAIL** (`contract says STRING, table has INT64`); format check **WARN** on 3,659 rows | none: staging reads both forms and always outputs `L4` |

## First three checks

1. **Read the contract lines in the dbt output.** They name the table, column, expected and actual type:
   `grep "contract says"` on the run log, or open the failed step in the nightly run.
2. **Look at the source.** Did the table really change, or did the load go wrong?
   ```sql
   select column_name, data_type from information_schema.columns
   where table_schema = 'hris' and table_name = 'hris_job_history' order by ordinal_position;
   select * from sim.state where key like 'drill:%';   -- a drill still running?
   ```
3. **Look at each hop.** `make check-ingestion` compares source, landing and BigQuery. In BigQuery:
   ```sql
   select column_name, data_type from raw_hris.INFORMATION_SCHEMA.COLUMNS
   where table_name = 'hris_job_history';
   ```

## Root cause

DDL in the source system that wasn't announced. In the drills it's the scripts:
`python -m simulations.drift_add_column`, `drift_rename_column`, `drift_type_change` (each takes
`--revert`).

## Fix

**Added column.** Decide whether anyone needs it. If they do, add it to the contract, to staging, to
the mart that should expose it, to the model docs and to the LookML view. If not, add it to the
contract anyway so the warning stops.

**Renamed column.** Ask the source owner whether it's permanent and for how long both names will
exist. During the transition, staging reads both:
```sql
coalesce(trim(work_location), trim(location)) as location,
```
Rows written since the rename only have `work_location`, and older rows only have `location`, so the
coalesce covers both. Add `work_location` to the contract, then drop `location` from staging and the
contract once the source drops it. With the bootstrap load only the new name exists, so staging reads
`work_location` straight away.

**Type change.** Staging already accepts `L4` and `4` and outputs `L4`, so nothing downstream changes.
Confirm with the source owner which form is the standard. Then update the contract type (`int64` if
the landed column really changed) and the format check (accept bare numbers if that's the new
standard).

**Then, in all three cases:** `make ingest`, `make dbt-build`, and confirm it's green.

## Prevention

- The [data contract](data-contract-hris.md): breaking changes are announced 2 weeks ahead, and renames
  run as a transition with both columns present.
- Additive changes are safe by design: Fivetran brings them in, and the contract only warns.
- Staging normalises fields whose format is known to vary. Here that's the job level, and department
  names have a mapping seed.
- Breaking changes are caught at the raw layer, before any mart is built, and the mart tables keep
  their last good version.

## Drill log

Run on 2026-10-14 against the source, loaded into BigQuery with the bootstrap load (`make ingest
SOURCE=direct`) and built with `dbt build --select source:hris.<table>+` (the headcount table left out
to save time). Each drill ran on its own, from a green build. "Skipped" counts staging plus every model
and test built on it.

| Drill | Detected by | Build result | Reverted |
| --- | --- | --- | --- |
| 1. add `work_mode` | additive contract warning on `hris_employees` | PASS 38, WARN 1, nothing skipped | yes, green after reload |
| 2. rename `location` | breaking contract failure + additive warning on `hris_job_history` | FAIL 1, WARN 1, 31 skipped | yes, green after reload |
| 3. retype `job_level` | breaking contract failure + format warning | FAIL 1, 31 skipped | yes, green after reload |

`make simulate-day` kept working during drills 2 and 3. It writes to whatever the live column is
called, and converts levels to the live column type, so the source kept moving while the drift was in
place. After all three were reverted, the full build passed: 114 of 114.

Still to do once the Fivetran connection is live: run the three drills through Fivetran, and record
the time from the change to the first failing nightly run.
