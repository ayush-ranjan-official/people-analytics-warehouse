"""Shared plumbing for the schema-drift and sync-failure drills.

Each drill breaks one thing in the source on purpose, records when it started (sim.state), runs one
simulated business day so the change flows downstream, and prints what to watch for. `--revert`
undoes it and prints how long it lasted. Keep any failure drill under ~36 hours: Neon drops a
replication slot that hasn't moved for ~40 hours, and Fivetran would then need a full re-sync.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg

from generator.load_to_postgres import connect, get_state, set_state, simulate_days


def started(conn: psycopg.Connection, name: str) -> datetime | None:
    value = get_state(conn, f"drill:{name}")
    return datetime.fromisoformat(value) if value else None


def run(name: str, summary: str, breaks: Callable[[psycopg.Connection], None],
        reverts: Callable[[psycopg.Connection], None], watch: list[str], recover: list[str]) -> int:
    ap = argparse.ArgumentParser(description=summary)
    ap.add_argument("--revert", action="store_true", help="undo the drill")
    ap.add_argument("--no-simulate", action="store_true", help="don't run a simulated day afterwards")
    args = ap.parse_args()

    with connect() as conn:
        began = started(conn, name)
        if args.revert:
            if began is None:
                print(f"{name}: not running, nothing to revert.")
                return 0
            with conn.transaction():
                reverts(conn)
                conn.execute("delete from sim.state where key = %s", (f"drill:{name}",))
            hours = (datetime.now(UTC) - began).total_seconds() / 3600
            print(f"{name}: reverted after {hours:.1f} hours.")
            for step in recover:
                print(f"  - {step}")
            return 0
        if began is not None:
            print(f"{name}: already running since {began:%Y-%m-%d %H:%M} UTC. Use --revert first.")
            return 1
        with conn.transaction():
            breaks(conn)
            set_state(conn, f"drill:{name}", datetime.now(UTC).isoformat())
    print(f"{name}: {summary}")
    if not args.no_simulate:
        simulate_days(1)
    print("Watch for:")
    for item in watch:
        print(f"  - {item}")
    print(f"Undo with: python -m simulations.{name} --revert")
    return 0
