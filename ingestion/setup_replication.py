"""Prepare the source and landing databases for Fivetran. Safe to re-run.

    python -m ingestion.setup_replication          # grants and the publication
    python -m ingestion.setup_replication --slot   # also the replication slot (right before connecting Fivetran)

Source (POSTGRES_URL): checks logical replication is on, gives the `fivetran` role read access to
schema `hris`, and creates publication `fivetran_pub` listing the eight tables. The slot is created
only on request: Neon drops a slot that nothing reads for ~40 hours.

Landing (LANDING_POSTGRES_URL): lets the `fivetran` role create its schema there.

The `fivetran` role is created in the Neon console for each project, because roles created with SQL
on Neon can't be given the REPLICATION attribute. Every grant is checked after it's made: on
Postgres, a GRANT from a role that can't give it is only a warning.

The source settings Fivetran tests are checked too. Neon's wal_sender_timeout is 10 s; Fivetran wants
0 or 5 min. Only the role itself may change its own default, so if FIVETRAN_SOURCE_PASSWORD is set
the script logs in as `fivetran` to set it; otherwise it prints the one statement to run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from generator.generate import COLUMNS

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_SCHEMA = "hris"
ROLE = "fivetran"
PUBLICATION = "fivetran_pub"
SLOT = "fivetran_pgoutput_slot"


def connect(var: str) -> psycopg.Connection:
    url = os.environ.get(var)
    if not url:
        sys.exit(f"{var} is not set (in .env or the environment)")
    return psycopg.connect(url, connect_timeout=15, autocommit=True)


CONSOLE_ROLES = "select the project, then the branch > Postgres database > Roles > Add role"


def require_role(conn: psycopg.Connection, where: str, replication: bool) -> None:
    row = conn.execute("select rolcanlogin, rolreplication from pg_roles where rolname = %s", (ROLE,)).fetchone()
    if row is None:
        sys.exit(f"No '{ROLE}' role on the {where} database. Create it in the Neon console "
                 f"({CONSOLE_ROLES}, name '{ROLE}'), keep the password for Fivetran, and re-run.")
    can_login, has_replication = row
    if not can_login or (replication and not has_replication):
        sys.exit(f"The {where} '{ROLE}' role can't log in or lacks REPLICATION, which happens when it's created "
                 f"with SQL. Delete it and recreate it in the Neon console ({CONSOLE_ROLES}).")


def check(conn: psycopg.Connection, query: str, params: tuple, failure: str) -> None:
    if not conn.execute(query, params).fetchone()[0]:
        sys.exit(f"Grant didn't take effect: {failure}. Run this with the database owner's login.")


def setup_source(conn: psycopg.Connection, make_slot: bool) -> None:
    wal_level = conn.execute("show wal_level").fetchone()[0]
    if wal_level != "logical":
        sys.exit(f"Logical replication is off on the source (wal_level = {wal_level}). In the Neon console open "
                 "the source project > Settings > Postgres > Logical replication > Enable, then re-run.")
    require_role(conn, "source", replication=True)
    role, schema = sql.Identifier(ROLE), sql.Identifier(SOURCE_SCHEMA)
    conn.execute(sql.SQL("grant usage on schema {} to {}").format(schema, role))
    conn.execute(sql.SQL("grant select on all tables in schema {} to {}").format(schema, role))
    conn.execute(sql.SQL("alter default privileges in schema {} grant select on tables to {}").format(schema, role))
    check(conn, "select has_schema_privilege(%s, %s, 'USAGE')", (ROLE, SOURCE_SCHEMA), f"USAGE on {SOURCE_SCHEMA}")
    for table in COLUMNS:
        check(conn, "select has_table_privilege(%s, %s, 'SELECT')", (ROLE, f"{SOURCE_SCHEMA}.{table}"),
              f"SELECT on {SOURCE_SCHEMA}.{table}")
    print(f"source: {ROLE} can read schema {SOURCE_SCHEMA}")
    check_settings(conn)

    wanted = [sql.SQL("{}.{}").format(schema, sql.Identifier(t)) for t in COLUMNS]
    exists = conn.execute("select 1 from pg_publication where pubname = %s", (PUBLICATION,)).fetchone()
    if exists is None:
        conn.execute(sql.SQL("create publication {} for table {}").format(
            sql.Identifier(PUBLICATION), sql.SQL(", ").join(wanted)))
        print(f"source: created publication {PUBLICATION}")
    published = {r[0] for r in conn.execute(
        "select tablename from pg_publication_tables where pubname = %s and schemaname = %s",
        (PUBLICATION, SOURCE_SCHEMA)).fetchall()}
    missing = [t for t in COLUMNS if t not in published]
    if missing:
        conn.execute(sql.SQL("alter publication {} add table {}").format(
            sql.Identifier(PUBLICATION),
            sql.SQL(", ").join(sql.SQL("{}.{}").format(schema, sql.Identifier(t)) for t in missing)))
        print(f"source: added {', '.join(missing)} back to {PUBLICATION}")
    print(f"source: publication {PUBLICATION} covers {len(COLUMNS)} tables")

    slot = conn.execute("""select active, wal_status, plugin, database = current_database()
                           from pg_replication_slots where slot_name = %s""", (SLOT,)).fetchone()
    if slot is None and make_slot:
        conn.execute("select pg_create_logical_replication_slot(%s, 'pgoutput')", (SLOT,))
        print(f"source: created replication slot {SLOT}. Connect Fivetran within ~40 hours, or Neon drops it.")
    elif slot is None:
        print("source: no replication slot. Run with --slot right before setting up the Fivetran connector. "
              "If Fivetran was already connected, it needs a full re-sync: a new slot can't resume the old one.")
    elif slot[2] != "pgoutput" or not slot[3]:
        sys.exit(f"source: slot {SLOT} uses plugin {slot[2]} or another database; drop it and re-run with --slot.")
    elif slot[1] == "lost":
        sys.exit(f"source: slot {SLOT} has lost WAL. Drop it, re-run with --slot, then trigger a Fivetran "
                 "historical re-sync.")
    else:
        print(f"source: replication slot {SLOT} exists (active={slot[0]}, wal_status={slot[1]})")


def check_settings(conn: psycopg.Connection) -> None:
    """The source settings Fivetran's connection test looks at, as the fivetran role will see them."""
    role_setting = conn.execute("""select coalesce(string_agg(cfg, ','), '') from pg_db_role_setting s
                                   join pg_roles r on r.oid = s.setrole, unnest(s.setconfig) cfg
                                   where r.rolname = %s""", (ROLE,)).fetchone()[0]
    if "wal_sender_timeout" not in role_setting:
        password = os.environ.get("FIVETRAN_SOURCE_PASSWORD", "")
        if password and password != "...":
            params = conninfo_to_dict(os.environ["POSTGRES_URL"])
            params.update(user=ROLE, password=password)
            with psycopg.connect(make_conninfo(**params), connect_timeout=15, autocommit=True) as as_role:
                as_role.execute("alter role current_user set wal_sender_timeout = '5min'")
            print(f"source: set wal_sender_timeout = 5min for {ROLE} (Neon's 10 s default makes Fivetran warn)")
        else:
            print(f"source: log in as {ROLE} and run: alter role current_user set wal_sender_timeout = '5min';")
    for name, ok in (("statement_timeout", {"0"}), ("idle_replication_slot_timeout", {"0"})):
        value = conn.execute(f"show {name}").fetchone()[0]
        if value not in ok:
            print(f"source: warning: {name} = {value}; Fivetran needs 0 (or a long value).")


def setup_landing(conn: psycopg.Connection) -> None:
    require_role(conn, "landing", replication=False)
    db = conn.execute("select current_database()").fetchone()[0]
    conn.execute(sql.SQL("grant create, temporary on database {} to {}").format(
        sql.Identifier(db), sql.Identifier(ROLE)))
    for privilege in ("CREATE", "TEMPORARY"):
        check(conn, f"select has_database_privilege(%s, %s, '{privilege}')", (ROLE, db), f"{privilege} on {db}")
    print(f"landing: {ROLE} can create schemas and temporary tables in database {db}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slot", action="store_true", help="also create the replication slot")
    args = ap.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    with connect("POSTGRES_URL") as conn:
        setup_source(conn, args.slot)
    with connect("LANDING_POSTGRES_URL") as conn:
        setup_landing(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
