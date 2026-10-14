"""Drill 1: a new column, work_mode (onsite / hybrid / remote), appears in hris_employees."""

import sys

from simulations.drill import run


def breaks(conn):
    conn.execute("alter table hris.hris_employees add column work_mode text")
    # HR backfills the new field for everyone currently employed; that's an in-place update.
    conn.execute("""update hris.hris_employees set work_mode = case
                        when e.location = 'Remote' then 'remote'
                        when abs(mod(hashtext(e.employee_id), 3)) = 0 then 'onsite'
                        else 'hybrid' end, updated_at = now()
                    from (select distinct on (employee_id) employee_id, location from hris.hris_job_history
                          order by employee_id, effective_from desc) as e
                    where e.employee_id = hris.hris_employees.employee_id and status = 'Active'""")


def reverts(conn):
    conn.execute("alter table hris.hris_employees drop column if exists work_mode")


if __name__ == "__main__":
    sys.exit(run(
        "drift_add_column", "added hris_employees.work_mode and backfilled it for active employees",
        breaks, reverts,
        watch=["Fivetran adds work_mode to raw_hris.hris_employees on the next sync (Allow all)",
               "`make ingest` copies it to BigQuery",
               "`make dbt-build`: the additive schema contract on source hris.employees warns about work_mode"],
        recover=["Fivetran keeps the column in the landing table; it's NULL for rows changed from now on",
                 "If work_mode is wanted: add it to stg_hris__employees, dim_employee, the docs and LookML"]))
