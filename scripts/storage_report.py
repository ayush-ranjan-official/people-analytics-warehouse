"""Storage written by this project, against the BigQuery sandbox's 10 GiB lifetime allowance.

    python scripts/storage_report.py            # report
    python scripts/storage_report.py --record   # also save the new writes to the ledger (the nightly run does)

The sandbox counts what has ever been written, not what's stored now ("a lifetime limit of 10 GiB ...
not refunded upon data deletion"), so this report counts writes:
- load jobs (raw tables, seeds) report their output bytes exactly;
- CREATE TABLE AS SELECT jobs (dbt table models) report rows written, converted to bytes with the
  target table's current row size;
- views and tests write nothing.

BigQuery only keeps about six months of job history, so the running total lives in a small ledger
table, <raw dataset>._storage_ledger (one row per day), rewritten by a load job on each --record.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion.load_to_bigquery import KEEP_DAYS, bigquery_client  # noqa: E402

MIB, GIB = 1024**2, 1024**3
LIFETIME_LIMIT = 10 * GIB
TARGET = 5 * GIB                     # leave half the allowance as headroom
LEDGER = "_storage_ledger"


def stored_now(client: bigquery.Client) -> tuple[int, dict[str, int]]:
    """Bytes stored per table now, keyed by full table id."""
    sizes = {}
    for dataset in client.list_datasets():
        for item in client.list_tables(dataset.reference):
            if item.table_type == "TABLE":
                table = client.get_table(item.reference)
                sizes[f"{table.project}.{table.dataset_id}.{table.table_id}"] = (table.num_bytes or 0,
                                                                                  table.num_rows or 0)
    return sum(b for b, _ in sizes.values()), sizes


def row_size(target: str, sizes: dict) -> float:
    """Average bytes per row of a CTAS target; a dropped table borrows a same-named one's size."""
    if target in sizes and sizes[target][1]:
        return sizes[target][0] / sizes[target][1]
    name = target.rsplit(".", 1)[-1]
    same = [(b, n) for t, (b, n) in sizes.items() if t.rsplit(".", 1)[-1] == name and n]
    return same[0][0] / same[0][1] if same else 0.0


def written_since(client: bigquery.Client, since: datetime | None, sizes: dict) -> tuple[dict, datetime | None]:
    """Bytes written per day by this account's successful jobs created after `since`."""
    per_day: dict[date, list[int]] = defaultdict(lambda: [0, 0, 0])     # load bytes, table bytes, jobs
    latest = since
    for job in client.list_jobs(min_creation_time=since, state_filter="done"):
        if since is not None and job.created <= since:
            continue
        latest = max(latest, job.created) if latest else job.created
        if job.error_result:
            continue
        day = job.created.date()
        if job.job_type == "load":
            per_day[day][0] += int(job._properties["statistics"].get("load", {}).get("outputBytes", 0))
            per_day[day][2] += 1
        elif job.job_type == "query" and job.statement_type == "CREATE_TABLE_AS_SELECT" and job.ddl_target_table:
            target = job.ddl_target_table
            rows = sum(s.records_written or 0 for s in job.query_plan if s.name.endswith(": Output"))
            per_day[day][1] += int(rows * row_size(f"{target.project}.{target.dataset_id}.{target.table_id}", sizes))
            per_day[day][2] += 1
    return per_day, latest


def read_ledger(client: bigquery.Client, table_id: str) -> tuple[dict, datetime | None, bigquery.Table | None]:
    try:
        table = client.get_table(table_id)
    except NotFound:
        return {}, None, None
    rows = {r["day"]: [r["load_bytes"], r["table_bytes"], r["jobs"]] for r in client.list_rows(table)}
    through = (table.labels or {}).get("through")
    return rows, (datetime.fromtimestamp(int(through) / 1000, UTC) if through else None), table


def write_ledger(client: bigquery.Client, table_id: str, existing: bigquery.Table | None,
                 days: dict, through: datetime | None, now: datetime) -> None:
    if existing is not None and existing.created < now - timedelta(days=30):
        client.delete_table(table_id)        # the sandbox counts 60 days from creation
    ordered = sorted(days)
    data = pa.table({"day": pa.array(ordered, pa.date32()),
                     "load_bytes": pa.array([days[d][0] for d in ordered], pa.int64()),
                     "table_bytes": pa.array([days[d][1] for d in ordered], pa.int64()),
                     "jobs": pa.array([days[d][2] for d in ordered], pa.int64())})
    with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
        pq.write_table(data, f.name)
        config = bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.PARQUET,
                                        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
        with open(f.name, "rb") as fh:
            client.load_table_from_file(fh, table_id, job_config=config).result()
    table = client.get_table(table_id)
    table.expires = now + timedelta(days=KEEP_DAYS)
    table.labels = {"through": str(int(through.timestamp() * 1000))} if through else {}
    table.description = "Bytes written per day, for the sandbox's lifetime storage limit (scripts/storage_report.py)."
    client.update_table(table, ["expires", "labels", "description"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", action="store_true", help="save the new writes to the ledger table")
    args = ap.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client, raw_dataset = bigquery_client()
    now = datetime.now(UTC)
    ledger_id = f"{raw_dataset}.{LEDGER}"

    stored, sizes = stored_now(client)
    days, through, ledger = read_ledger(client, ledger_id)
    new, latest = written_since(client, through, sizes)
    for day, (load, table, jobs) in new.items():
        days.setdefault(day, [0, 0, 0])
        days[day] = [days[day][0] + load, days[day][1] + table, days[day][2] + jobs]

    total = sum(load + table for load, table, _ in days.values())
    last_28 = sum(load + table for d, (load, table, _) in days.items() if d > now.date() - timedelta(days=28))
    span = min(28, (now.date() - min(days)).days + 1) if days else 1
    per_week = last_28 / span * 7

    print("Bytes written (lifetime, counts against the sandbox's 10 GiB):")
    for day in sorted(days)[-7:]:
        load, table, jobs = days[day]
        print(f"  {day}  loads {load / MIB:7.1f} MiB  tables {table / MIB:7.1f} MiB  ({jobs} jobs)")
    print(f"  total since {min(days) if days else '-'}: {total / MIB:.1f} MiB ({total / LIFETIME_LIMIT:.1%} of 10 GiB)")
    print(f"  recent rate: {per_week / MIB:.1f} MiB a week (average over the last {span} days)")
    if per_week > 0:
        for label, limit in (("5 GiB target", TARGET), ("10 GiB limit", LIFETIME_LIMIT)):
            weeks = (limit - total) / per_week
            when = (now + timedelta(weeks=weeks)).date() if weeks > 0 else "already reached"
            print(f"  at this rate the {label} is reached: {when}")
    print(f"Stored now: {stored / MIB:.1f} MiB across {len(sizes)} tables (views store nothing)")

    if args.record:
        write_ledger(client, ledger_id, ledger, days, latest, now)
        print(f"Ledger {ledger_id} updated through {latest:%Y-%m-%d %H:%M} UTC.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
