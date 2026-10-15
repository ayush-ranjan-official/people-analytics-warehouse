"""Drill 5: ats_applications stops replicating while every other table keeps syncing.

Neon's console-created roles can read every table (pg_read_all_data), so revoking SELECT does
nothing. Removing the table from the replication publication has the same effect Fivetran would
see from a lost grant: that one table stops changing.
"""

import sys

from simulations.drill import run


def breaks(conn):
    conn.execute("alter publication fivetran_pub drop table hris.ats_applications")


def reverts(conn):
    conn.execute("alter publication fivetran_pub add table hris.ats_applications")


if __name__ == "__main__":
    sys.exit(run(
        "fail_permissions", "removed hris.ats_applications from the fivetran_pub publication",
        breaks, reverts,
        watch=["Every table keeps syncing except ats_applications",
               "`make freshness` flags only ats.applications (warn at 28 h, error at 34 h)",
               "`make check-ingestion` shows source and landing row counts drifting apart for that table"],
        recover=["Sync, then run `make check-ingestion`. Changes made while the table was out of the publication "
                 "were never sent; in the drill Fivetran re-copied the whole table on its next sync, but if it's "
                 "still behind, re-sync the ats_applications table in Fivetran",
                 "`make setup-replication` also re-adds a missing table to the publication"]))
