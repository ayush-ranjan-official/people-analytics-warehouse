"""Phase 0 done-check: prove we can connect to the source Postgres and write to it.

Creates a scratch table, inserts one row, reads it back, then drops the table.
Reads POSTGRES_URL from the environment (or .env when run directly).
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_TABLE = "public._phase0_connection_check"


def describe(params: dict) -> str:
    """user@host/db for logs. Built from parsed fields, so the password can never leak into it."""
    return f"{params.get('user')}@{params.get('host')}/{params.get('dbname')}"


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    url = os.environ.get("POSTGRES_URL", "")
    if not url or "USER:PASSWORD@HOST" in url:
        print("POSTGRES_URL is not set in .env")
        return 1

    # Parses both URL and key=value forms, the same way libpq will.
    params = conninfo_to_dict(url)
    if "-pooler" in str(params.get("host", "")):
        print(f"POSTGRES_URL uses Neon's pooled host ({params['host']}). Use the direct host "
              "(drop '-pooler' from the hostname): Fivetran's logical replication needs it.")
        return 1

    print(f"Connecting to {describe(params)}")
    # One transaction: committed on success, rolled back on any error, so nothing is left behind.
    with psycopg.connect(url, connect_timeout=15) as conn:
        version = conn.execute("show server_version").fetchone()[0]
        user, db = conn.execute("select current_user, current_database()").fetchone()
        print(f"Connected: Postgres {version}, user={user}, database={db}")

        conn.execute(
            f"create table {SCRATCH_TABLE} "
            "(id serial primary key, note text not null, inserted_at timestamptz not null default now())"
        )
        conn.execute(f"insert into {SCRATCH_TABLE} (note) values (%s)", ("phase 0 check",))
        row = conn.execute(f"select id, note, inserted_at from {SCRATCH_TABLE}").fetchone()
        print(f"Inserted and read back: id={row[0]}, note={row[1]!r}, inserted_at={row[2]:%Y-%m-%d %H:%M:%S %Z}")
        conn.execute(f"drop table {SCRATCH_TABLE}")

    print("Postgres check passed (scratch table dropped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
