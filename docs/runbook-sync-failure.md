# Runbook: Fivetran sync failures

For when data stops arriving: the whole connection fails (**credentials**), or one table quietly stops
updating while the rest keep syncing (**a table drops out of replication**). Both were rehearsed with
drill scripts in [simulations/](../simulations/).

## How it's detected

| Signal | Credentials broken | One table stopped |
| --- | --- | --- |
| Fivetran connection status | sync fails within seconds, 0 tables extracted (the connection can't log in) | no error: Fivetran doesn't know it's missing changes |
| Fivetran email | after the **second** failed sync in a row (connection errors) | none |
| `make check-ingestion` | every table that changed is ahead in the source, behind in landing | only `ats_applications` differs, and its last sync is older than the other tables': the output points at the publication |
| `make freshness` (nightly, never blocks) | `ats.applications` warns at 28 h and errors at 34 h; `hris.job_history` at 10 and 14 days | the same thresholds, but only `ats.applications` is flagged |
| dbt tests | pass: the warehouse is consistent, just old | pass |

Freshness is measured on `_fivetran_synced`, which only moves when a row changes. Applications change
every business day, so a 28-hour warning means one missed day. Job records change less often, so that
threshold is days. Tables that can go a week unchanged have no threshold, because they'd raise false
alarms.

The nightly run reports freshness but doesn't fail on it. A stalled sync leaves the warehouse correct
as of the last sync, so the right response is a warning rather than a red build.

## First three checks

1. **Fivetran:** the connection's status page and the latest sync's log (or the `fivetran_metadata`
   schema in the landing database). A credentials failure names the user. A missing table shows no
   error at all, which is itself the clue.
2. **`make check-ingestion`:** which tables are behind, and at which hop (source → landing, or
   landing → BigQuery). If only the BigQuery hop is behind, Fivetran is fine and the nightly load
   didn't run.
3. **The source side of replication:**
   ```sql
   select tablename from pg_publication_tables where pubname = 'fivetran_pub';   -- all 8 tables?
   select slot_name, active, wal_status, restart_lsn from pg_replication_slots;  -- slot still there?
   select * from sim.state where key like 'drill:%';                             -- a drill running?
   ```
   Then try logging in as `fivetran` with the password Fivetran has.

## Root cause

- **Credentials:** the password for the `fivetran` role was changed and Fivetran wasn't updated. Drill:
  `python -m simulations.fail_credentials`. The role changes its own password, because Neon's owner
  role can't alter a role created in the console. The temporary password is kept in a 0600 file under
  `~/.config/people-analytics/`, so the drill can be reverted.
- **One table stopped:** `ats_applications` was removed from the publication Fivetran reads. Drill:
  `python -m simulations.fail_permissions`. On a normal Postgres the same failure comes from revoking
  SELECT. Neon's console roles have `pg_read_all_data`, so a revoke does nothing there, and dropping
  the table from the publication is the equivalent ([platform notes](platform-notes.md)).

## Fix

**Credentials.** Put the current password into the Fivetran connection settings, run Save & Test,
then Sync now. Fivetran resumes from the replication slot, so nothing is lost if the slot survived.

**One table stopped.** Run `make setup-replication`, which re-adds any missing table to the publication,
then sync. Changes made while the table was out of the publication were never sent through the slot,
so they can't simply be resumed. In the drill, Fivetran's next ordinary sync re-copied the whole table
by itself (every row got the new sync time), which brought back the missed inserts and updates. Don't
rely on that: confirm with `make check-ingestion`, and if the table is still behind, **re-sync that
table** in Fivetran.

**If the slot is gone.** Neon drops a slot after about 40 hours without progress. Recreate it with
`make setup-replication SLOT=1` and run a full re-sync in Fivetran.

**Then:** `make ingest`, `make dbt-build`, and confirm `make freshness` is green and
`make check-ingestion` says all hops agree.

## Prevention

- Credential rotation has a checklist, and Fivetran is on it. Postgres holds one password per role, so
  change it in the source and update Fivetran straight away, between two scheduled syncs.
- `make setup-replication` is safe to re-run. Run it after any DDL on the source.
- Turn on Fivetran's failure emails (Notifications > Connections), and keep freshness in the nightly
  run.
- Fix any broken sync within about 36 hours, so the slot survives and no full re-sync is needed.

## Drill log

Run on 2026-10-15 with the live Fivetran connection (logical replication, 6-hour schedule), syncs
started by hand so each step could be timed. Times are UTC.

**Drill 4: the password changed** (`make drill NAME=fail_credentials`)

| Time | What happened |
| --- | --- |
| 06:20:25 | `fivetran` role's password changed; one business day simulated at the source |
| 06:21:58 | Fivetran sync **failed** after 16 s, 0 tables extracted |
| 06:23:08 | `make check-ingestion` exits 1: 4 tables behind (positions 1, requisitions 1, applications 31, stages 86 rows) |
| 06:23:29 | password restored (`make drill NAME=fail_credentials REVERT=1`) |
| 06:24 | next sync succeeded: 4 tables, 175 rows (the missed inserts and updates), resumed from the slot, no re-sync |
| 06:26:24 | `make ingest`; all hops agree |

Six minutes from break to recovered. On the 6-hour schedule the failure would surface at the next
scheduled sync instead.

**Drill 5: one table out of replication** (`make drill NAME=fail_permissions`)

| Time | What happened |
| --- | --- |
| 06:26:37 | `ats_applications` removed from `fivetran_pub`; one business day simulated (33 new and 42 updated applications, plus job history and stage changes) |
| 06:27:28 | Fivetran sync **successful**: 3 tables, 82 rows. `ats_applications` isn't in it, and nothing warns |
| 06:29:05 | `make check-ingestion` exits 1: only `ats_applications` is behind (41,228 at the source, 41,195 landed) |
| 06:29:27 | table re-added to the publication |
| 06:30 | next ordinary sync re-copied the whole table: all 41,228 rows carry the 06:30 sync time; a row-by-row compare with the source finds no differences |
| 06:32:02 | `make ingest`; all hops agree |

This is the failure that matters most: Fivetran reported success throughout, and only the warehouse's
own check found the gap.

**Not covered by these runs.** Fivetran's failure emails weren't set up (in production they'd go to the
team's alert channel after the second failed sync). Freshness warnings need the data to be 28 hours
old, so a drill would have to stay broken for over a day; the thresholds are set in
[_sources.yml](../dbt/models/staging/_sources.yml).
