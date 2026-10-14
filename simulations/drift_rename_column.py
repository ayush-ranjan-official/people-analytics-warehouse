"""Drill 2: hris_job_history.location is renamed to work_location."""

import sys

from generator.load_to_postgres import set_state
from simulations.drill import run

KEY = "rename:hris_job_history.location"


def breaks(conn):
    conn.execute("alter table hris.hris_job_history rename column location to work_location")
    set_state(conn, KEY, "work_location")      # so simulate-day keeps writing job history


def reverts(conn):
    conn.execute("alter table hris.hris_job_history rename column work_location to location")
    conn.execute("delete from sim.state where key = %s", (KEY,))


if __name__ == "__main__":
    sys.exit(run(
        "drift_rename_column", "renamed hris_job_history.location to work_location",
        breaks, reverts,
        watch=["Fivetran adds a work_location column; the old location column stays, NULL for rows changed "
               "from now on",
               "`make dbt-build`: not_null on dim_employee.location fails for the new job records, and the "
               "additive schema contract on source hris.job_history warns about work_location",
               "With the bootstrap load, location disappears instead: the breaking contract fails"],
        recover=["During the transition, stg_hris__job_history reads coalesce(work_location, location)",
                 "Note it in docs/data-contract-hris.md, then re-run `make dbt-build`"]))
