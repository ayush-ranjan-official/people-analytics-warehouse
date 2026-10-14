"""Compare the hops of ingestion: source Postgres -> Fivetran landing -> BigQuery raw_hris.

    python -m ingestion.check_ingestion                   # source, landing and BigQuery
    python -m ingestion.check_ingestion --source direct   # bootstrap loads: source and BigQuery

For each source table:
- the source row count equals the landing rows Fivetran hasn't marked deleted;
- the BigQuery copy was made from the current landing table (same rows, latest sync, columns);
- both copies carry Fivetran's _fivetran_synced and _fivetran_deleted columns;
- the BigQuery table isn't close to its sandbox expiry.

Right after `make simulate-day` the source is ahead of the landing database until Fivetran's next
sync, and the landing database is ahead of BigQuery until `make ingest`; the output says which.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from psycopg import sql

from generator.generate import COLUMNS
from ingestion.load_to_bigquery import LANDING_SCHEMA, RELOAD_WITHIN_DAYS, SOURCE_SCHEMA, Origin, bigquery_client

REPO_ROOT = Path(__file__).resolve().parent.parent
FIVETRAN_COLUMNS = {"_fivetran_synced", "_fivetran_deleted"}


def count(conn: psycopg.Connection, schema: str, table: str, where: str = "") -> int:
    query = sql.SQL("select count(*) from {}.{} " + where).format(sql.Identifier(schema), sql.Identifier(table))
    return conn.execute(query).fetchone()[0]


def columns_of(conn: psycopg.Connection, schema: str, table: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "select column_name from information_schema.columns where table_schema = %s and table_name = %s",
        (schema, table))}


def bigquery_side(client: bigquery.Client, table_id: str,
                  now: datetime) -> tuple[int | None, set[str], dict, int | None]:
    try:
        bq = client.get_table(table_id)
    except NotFound:
        return None, set(), {}, None
    days_left = (bq.expires - now).days if bq.expires else None
    return bq.num_rows, {f.name for f in bq.schema}, bq.labels or {}, days_left


def shown(v: int | None) -> str:
    return f"{v:,}" if v is not None else "-"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=["fivetran", "direct"], default="fivetran")
    args = ap.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    origin = Origin(args.source)
    client, dataset = bigquery_client()
    now = datetime.now(UTC)
    problems: list[str] = []

    print(f"Mode: {origin.name}" + ("  (bootstrap: BigQuery loaded straight from the source)" if origin.direct else ""))
    print(f"{'table':<24} {'source':>8} {'landing':>8} {'deleted':>8} {'bigquery':>9}  last synced (UTC)   expires in")
    with psycopg.connect(os.environ["POSTGRES_URL"], connect_timeout=15, autocommit=True) as src, \
            psycopg.connect(os.environ[origin.url_var], connect_timeout=15, autocommit=True) as land:
        landed = set(origin.tables(land))
        for table in COLUMNS:
            n_src = count(src, SOURCE_SCHEMA, table)
            n_bq, bq_cols, labels, days_left = bigquery_side(client, f"{dataset}.{table}", now)
            n_land = n_deleted = synced = expected = None

            if table not in landed:
                problems.append(f"{table}: not in the landing database yet (Fivetran hasn't synced it)")
            elif origin.direct:
                expected = origin.state(land, table)
                if n_bq is not None and n_bq != n_src:
                    problems.append(f"{table}: source has {n_src:,} rows, BigQuery {n_bq:,} "
                                    "(run make ingest SOURCE=direct)")
            else:
                land_cols = columns_of(land, LANDING_SCHEMA, table)
                n_land = count(land, LANDING_SCHEMA, table)
                if FIVETRAN_COLUMNS - land_cols:
                    problems.append(f"{table}: landing table lacks {sorted(FIVETRAN_COLUMNS - land_cols)}")
                else:
                    n_deleted = count(land, LANDING_SCHEMA, table, "where _fivetran_deleted")
                    synced = land.execute(sql.SQL("select max(_fivetran_synced) from {}.{}").format(
                        sql.Identifier(LANDING_SCHEMA), sql.Identifier(table))).fetchone()[0]
                    if n_src != n_land - n_deleted:
                        problems.append(f"{table}: source has {n_src:,} rows, landing has {n_land - n_deleted:,} "
                                        "live (wait for or trigger a Fivetran sync)")
                expected = origin.state(land, table)

            if n_bq is None:
                problems.append(f"{table}: not in BigQuery yet (run make ingest)")
            else:
                if expected is not None and any(labels.get(k) != v for k, v in expected.items()):
                    problems.append(f"{table}: BigQuery copy is older than its {origin.name} source (run make ingest)")
                if FIVETRAN_COLUMNS - bq_cols:
                    problems.append(f"{table}: BigQuery table lacks {sorted(FIVETRAN_COLUMNS - bq_cols)}")
                if days_left is not None and days_left < RELOAD_WITHIN_DAYS:
                    problems.append(f"{table}: BigQuery copy expires in {days_left} days (run make ingest)")

            shown_synced = f"{synced.astimezone(UTC):%Y-%m-%d %H:%M}" if synced else "-"
            shown_days = f"{days_left} days" if days_left is not None else "-"
            print(f"{table:<24} {n_src:>8,} {shown(n_land):>8} {shown(n_deleted):>8} {shown(n_bq):>9}  "
                  f"{shown_synced:<16}   {shown_days}")

    if problems:
        print("\n" + "\n".join(f"  {p}" for p in problems))
        return 1
    print("\nAll hops agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
