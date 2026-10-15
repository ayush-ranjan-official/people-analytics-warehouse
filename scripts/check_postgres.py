"""Prove we can connect to the source and landing Postgres databases and write to them.

For each one: creates a scratch table, inserts a row, reads it back, then drops the table.
Reads POSTGRES_URL (source) and LANDING_POSTGRES_URL (Fivetran's landing database) from the
environment, or from .env when run directly. The landing database is skipped when LANDING_POSTGRES_URL is unset.
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_TABLE = "public._connection_check"
DATABASES = [("POSTGRES_URL", "source", True), ("LANDING_POSTGRES_URL", "landing", False)]


def describe(params: dict) -> str:
    """user@host/db for logs. Built from parsed fields, so the password can never leak into it."""
    return f"{params.get('user')}@{params.get('host')}/{params.get('dbname')}"


def check(var: str, label: str) -> bool:
    url = os.environ[var]
    # Parses both URL and key=value forms, the same way libpq will.
    params = conninfo_to_dict(url)
    if "-pooler" in str(params.get("host", "")):
        print(f"{label}: {var} uses Neon's pooled host ({params['host']}). Use the direct host "
              "(drop '-pooler' from the hostname): Fivetran needs it.")
        return False

    # One transaction: committed on success, rolled back on any error, so nothing is left behind.
    with psycopg.connect(url, connect_timeout=15) as conn:
        version = conn.execute("show server_version").fetchone()[0]
        conn.execute(f"create table {SCRATCH_TABLE} (id int primary key, inserted_at timestamptz default now())")
        conn.execute(f"insert into {SCRATCH_TABLE} (id) values (1)")
        inserted_at = conn.execute(f"select inserted_at from {SCRATCH_TABLE}").fetchone()[0]
        conn.execute(f"drop table {SCRATCH_TABLE}")
    print(f"{label}: {describe(params)}, Postgres {version}, wrote and read a row at {inserted_at:%H:%M:%S %Z}")
    return True


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    ok = True
    for var, label, required in DATABASES:
        url = os.environ.get(var, "")
        if not url or "USER:PASSWORD@HOST" in url:
            if required:
                print(f"{label}: {var} is not set")
                ok = False
            else:
                print(f"{label}: {var} not set yet (skipped)")
            continue
        ok = check(var, label) and ok
    print("Postgres check passed." if ok else "Postgres check failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
