"""Apply generator/fix_source.sql to the source database in one transaction.

    python -m generator.fix_source            # show what would change, then roll back
    python -m generator.fix_source --apply    # apply it

Each statement's row count is printed. Nothing is committed without --apply.
"""

import argparse
import re
import sys
from pathlib import Path

import psycopg

from generator.load_to_postgres import connect

SQL_FILE = Path(__file__).resolve().parent / "fix_source.sql"


def numbered_steps(text: str) -> list[tuple[str, str]]:
    """(label, statement) for each numbered step: a "-- N. ..." comment followed by one statement."""
    steps, label, body = [], None, []
    for line in text.splitlines():
        if match := re.match(r"--\s*(\d+\.\s.*)", line):
            label = match.group(1)
        elif not line.startswith("--") and line.strip() and label:
            body.append(line)
            if line.rstrip().endswith(";"):
                steps.append((label, "\n".join(body)))
                label, body = None, []
    return steps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="commit the changes")
    args = ap.parse_args()
    with connect() as conn, conn.transaction() as tx:
        for label, stmt in numbered_steps(SQL_FILE.read_text()):
            print(f"{conn.execute(stmt).rowcount:>4} rows  {label[:95]}")
        if not args.apply:
            print("Dry run: rolled back. Re-run with --apply to commit.")
            raise psycopg.Rollback(tx)
    if args.apply:
        print("Applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
