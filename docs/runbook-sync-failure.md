# Runbook: Fivetran sync failures

For when data stops arriving: the whole connection fails (**credentials**), or one table quietly stops
updating while the rest keep syncing (**a table drops out of replication**). Both were rehearsed with
drill scripts in [simulations/](../simulations/).

## How it's detected

| Signal | Credentials broken | One table stopped |
| --- | --- | --- |
| Fivetran connection status | sync fails: `password authentication failed for user "fivetran"` | no error: Fivetran doesn't know it's missing changes |
| Fivetran email | after the **second** failed sync in a row (connection errors) | none |
| `make check-ingestion` | every table that changed is ahead in the source, behind in landing | only `ats_applications` differs between source and landing |
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

**One table stopped.** Run `make setup-replication`, which re-adds any missing table to the publication.
Then **re-sync that table** in Fivetran. Changes made while it was out of the publication were never
sent, so resuming alone leaves a gap.

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

Run on 2026-10-14 against the source. The Fivetran connection isn't live yet, so these runs check the
break and the revert. They don't check the alerting.

| Drill | Break | Check during the drill | Revert | Check after |
| --- | --- | --- | --- | --- |
| 4. credentials | `fivetran` password changed to a random temporary one | login with the configured password **fails**; temporary file is 0600 | password restored | login **works**; temporary file removed |
| 5. one table | `ats_applications` dropped from `fivetran_pub` | publication has **7** tables, `ats_applications` missing | table re-added | publication has **8** tables |

The bootstrap load reads the source directly, so these two failures don't reach BigQuery and freshness
can't see them. `_fivetran_synced` is simply the load time. Once Fivetran is live, the plan is: run
each drill for a full day, record the time of the first Fivetran email and the first freshness warning,
then add screenshots of the failed sync and of the freshness output here.
