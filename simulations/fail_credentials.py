"""Drill 4: the password Fivetran uses for the source is rotated without telling Fivetran.

The fivetran role changes its own password (Neon's owner role can't change a console-created role).
The temporary password is kept in a local file outside the repo so --revert can log in and restore
the original from FIVETRAN_SOURCE_PASSWORD.
"""

import os
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from simulations.drill import run

TEMP_PASSWORD_FILE = Path.home() / ".config" / "people-analytics" / "drill_fivetran_password"


def as_fivetran(password: str) -> psycopg.Connection:
    params = conninfo_to_dict(os.environ["POSTGRES_URL"])
    params.update(user="fivetran", password=password)
    return psycopg.connect(make_conninfo(**params), connect_timeout=15, autocommit=True)


def set_password(old: str, new: str) -> None:
    with as_fivetran(old) as conn:
        conn.execute(sql.SQL("alter role current_user password {}").format(sql.Literal(new)))


def breaks(conn):
    original = os.environ.get("FIVETRAN_SOURCE_PASSWORD", "")
    if not original or original == "...":
        sys.exit("Set FIVETRAN_SOURCE_PASSWORD in .env first: --revert needs it to restore the password.")
    temporary = secrets.token_urlsafe(24)
    TEMP_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMP_PASSWORD_FILE.write_text(temporary)
    TEMP_PASSWORD_FILE.chmod(0o600)
    set_password(original, temporary)


def reverts(conn):
    set_password(TEMP_PASSWORD_FILE.read_text(), os.environ["FIVETRAN_SOURCE_PASSWORD"])
    TEMP_PASSWORD_FILE.unlink()


if __name__ == "__main__":
    sys.exit(run(
        "fail_credentials", "rotated the fivetran role's password on the source",
        breaks, reverts,
        watch=["Fivetran's next sync fails authentication; it emails after the SECOND failed sync",
               "BigQuery stops getting new rows; `make freshness` errors once data is 34 h old (applications)",
               "Revert within ~36 hours, or Neon drops the replication slot"],
        recover=["Fivetran resumes from the slot on its next sync (or click 'Sync now')",
                 "In a real rotation: update the password in Fivetran's connection settings, then sync"]))
