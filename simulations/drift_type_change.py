"""Drill 3: hris_job_history.job_level changes from text ('L4') to an integer (4)."""

import sys

from simulations.drill import run


def breaks(conn):
    conn.execute("""alter table hris.hris_job_history alter column job_level type integer
                    using nullif(regexp_replace(job_level, '\\D', '', 'g'), '')::integer""")


def reverts(conn):
    conn.execute("""alter table hris.hris_job_history alter column job_level type text
                    using 'L' || job_level::text""")


if __name__ == "__main__":
    sys.exit(run(
        "drift_type_change", "changed hris_job_history.job_level from text to integer",
        breaks, reverts,
        watch=["Fivetran keeps the landing column as text (it only widens types); new rows arrive as '4'",
               "`make dbt-build`: the job_level format check on source hris.job_history warns; with the "
               "bootstrap load the column really becomes INT64 and the schema contract fails",
               "Staging turns 4 back into L4, so the marts are unaffected"],
        recover=["Confirm the new format with the source owner, then update the contract type and the "
                 "format check (docs/runbook-schema-drift.md)"]))
