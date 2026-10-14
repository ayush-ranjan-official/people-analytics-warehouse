"""Load the generated history into the source Postgres, or apply one more simulated day.

    python -m generator.load_to_postgres                  # first load from generator/output
    python -m generator.load_to_postgres --simulate-day   # next business day of hires, moves, exits
    python -m generator.load_to_postgres --simulate-day --days 3

The first load replaces every row in one transaction (DELETE + COPY, never DROP). Once Fivetran
replicates the source (publication `fivetran_pub` exists) it refuses to run without --force,
because the source must then change only through row-level updates.

--simulate-day re-runs the deterministic simulation up to the next business date, compares the
data with the previous day, and writes only what changed. Changed employee rows get
updated_at = now(), so Fivetran picks them up incrementally.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from generator.generate import COLUMNS, PRIMARY_KEYS, fingerprint, load_config, simulate, tables_as_of

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SCHEMA = "hris"
PUBLICATION = "fivetran_pub"


def connect() -> psycopg.Connection:
    load_dotenv(REPO_ROOT / ".env")
    url = os.environ.get("POSTGRES_URL")
    if not url:
        sys.exit("POSTGRES_URL is not set (in .env or the environment)")
    conn = psycopg.connect(url, connect_timeout=15)
    conn.execute("set time zone 'UTC'")
    return conn


def publication_exists(conn: psycopg.Connection) -> bool:
    return conn.execute("select 1 from pg_publication where pubname = %s", (PUBLICATION,)).fetchone() is not None


def live_columns(conn: psycopg.Connection, table: str) -> list[str]:
    rows = conn.execute("""select column_name from information_schema.columns
                           where table_schema = %s and table_name = %s order by ordinal_position""",
                        (SCHEMA, table)).fetchall()
    return [r[0] for r in rows]


def get_state(conn: psycopg.Connection, key: str) -> str | None:
    if conn.execute("select to_regclass('sim.state')").fetchone()[0] is None:
        return None
    row = conn.execute("select value from sim.state where key = %s", (key,)).fetchone()
    return row[0] if row else None


def count_rows(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(sql.SQL("select count(*) from {}.{}").format(
        sql.Identifier(SCHEMA), sql.Identifier(table))).fetchone()[0]


def set_state(conn: psycopg.Connection, key: str, value: str) -> None:
    conn.execute("""insert into sim.state (key, value) values (%s, %s)
                    on conflict (key) do update set value = excluded.value, updated_at = now()""", (key, value))


# ---------------------------------------------------------------------------- first load
def first_load(force: bool) -> int:
    out = HERE / "output"
    meta_path = out / "meta.json"
    if not meta_path.exists():
        sys.exit("generator/output is empty: run `make generate` first")
    meta = json.loads(meta_path.read_text())
    if meta.get("fingerprint") != fingerprint(load_config()):
        sys.exit("generator/output was made with a different config.yaml, numpy or Faker: run `make generate` first")

    with connect() as conn:
        if publication_exists(conn) and not force:
            sys.exit(f"Publication {PUBLICATION} exists, so Fivetran is replicating this database. A full reload "
                     "would delete and re-insert every row. Change the source with --simulate-day or a reviewed "
                     "SQL patch instead, or pass --force if you really mean it.")
        conn.execute((HERE / "schema.sql").read_text())
        for table, cols in COLUMNS.items():
            live = live_columns(conn, table)
            if live != cols:
                sys.exit(f"{SCHEMA}.{table} has columns {live}, expected {cols}. "
                         "The source schema has drifted; revert it before a full load.")

        started = time.time()
        with conn.transaction():
            for table in reversed(COLUMNS):
                conn.execute(sql.SQL("delete from {}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(table)))
            for table, cols in COLUMNS.items():
                copy_sql = sql.SQL("copy {}.{} ({}) from stdin with (format csv, header true, null '')").format(
                    sql.Identifier(SCHEMA), sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)))
                with conn.cursor().copy(copy_sql) as copy, (out / f"{table}.csv").open("rb") as f:
                    while chunk := f.read(1 << 20):
                        copy.write(chunk)
            set_state(conn, "business_date", meta["as_of"])
            set_state(conn, "fingerprint", meta["fingerprint"])

        print(f"Loaded data as of {meta['as_of']} into {SCHEMA} in {time.time() - started:.1f}s:")
        for table in COLUMNS:
            print(f"  {table:<24} {count_rows(conn, table):>7,} rows")
    return 0


# ---------------------------------------------------------------------------- one more day
def diff(before: list[dict], after: list[dict], pk: str) -> tuple[list[dict], list[dict]]:
    old = {r[pk]: r for r in before}
    inserted, updated = [], []
    for row in after:
        prev = old.get(row[pk])
        if prev is None:
            inserted.append(row)
        elif prev != row:
            updated.append(row)
    missing = old.keys() - {r[pk] for r in after}
    if missing:
        raise RuntimeError(f"rows disappeared between days (pk {pk}): {sorted(missing)[:5]}")
    return inserted, updated


def upsert(conn: psycopg.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = COLUMNS[table]
    live = live_columns(conn, table)
    missing = [c for c in cols if c not in live]
    if missing:
        raise RuntimeError(f"{SCHEMA}.{table} is missing columns {missing}; the source schema has drifted")
    pk = PRIMARY_KEYS[table]
    now_cols = {"updated_at"} if table == "hris_employees" else set()
    values = [sql.SQL("now()") if c in now_cols else sql.Placeholder() for c in cols]
    stmt = sql.SQL("insert into {}.{} ({}) values ({}) on conflict ({}) do update set {}").format(
        sql.Identifier(SCHEMA), sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, cols)), sql.SQL(", ").join(values), sql.Identifier(pk),
        sql.SQL(", ").join(sql.SQL("{0} = excluded.{0}").format(sql.Identifier(c)) for c in cols if c != pk))
    params = [[r[c] for c in cols if c not in now_cols] for r in rows]
    with conn.cursor() as cur:
        cur.executemany(stmt, params)


def check_starting_point(conn: psycopg.Connection, cfg: dict, before: dict[str, list[dict]]) -> None:
    """The diff is only valid if the source holds exactly the history it starts from."""
    if get_state(conn, "fingerprint") != fingerprint(cfg):
        sys.exit("The source was loaded from a different config.yaml, numpy or Faker version, so the next day "
                 "wouldn't continue the same history. Revert the change, or `make generate load` before Fivetran.")
    wrong = [f"{t}: {n:,} rows, expected {len(before[t]):,}"
             for t in COLUMNS if (n := count_rows(conn, t)) != len(before[t])]
    if wrong:
        sys.exit("The source doesn't match the simulated history it should continue from:\n  " + "\n  ".join(wrong)
                 + "\nIf rows were fixed at the source on purpose, update config.yaml to match (e.g. turn off "
                 "planted_issues).")


def simulate_days(days: int) -> int:
    cfg = load_config()
    for _ in range(days):
        with connect() as conn:
            current = get_state(conn, "business_date")
            if current is None:
                sys.exit("The source hasn't been loaded: run `make load` first")
            day = date.fromisoformat(current) + timedelta(days=1)
            started = time.time()
            wf, apps = simulate(cfg, day)
            before, _ = tables_as_of(cfg, wf, apps, day - timedelta(days=1), check=False)
            after, _ = tables_as_of(cfg, wf, apps, day)
            check_starting_point(conn, cfg, before)
            changes = {t: diff(before[t], after[t], PRIMARY_KEYS[t]) for t in COLUMNS}
            with conn.transaction():
                for table in COLUMNS:
                    inserted, updated = changes[table]
                    upsert(conn, table, inserted + updated)
                set_state(conn, "business_date", day.isoformat())
            summary = ", ".join(f"{t} +{len(i)}/~{len(u)}" for t, (i, u) in changes.items() if i or u)
            print(f"{day}: {summary or 'no changes'}  ({time.time() - started:.1f}s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--simulate-day", action="store_true", help="apply the next business day of changes")
    ap.add_argument("--days", type=int, default=1, help="with --simulate-day: how many days to apply")
    ap.add_argument("--force", action="store_true", help="allow a full reload while Fivetran replicates the source")
    args = ap.parse_args()
    return simulate_days(args.days) if args.simulate_day else first_load(args.force)


if __name__ == "__main__":
    sys.exit(main())
