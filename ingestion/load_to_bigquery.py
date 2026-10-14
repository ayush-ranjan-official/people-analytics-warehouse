"""Copy the Fivetran landing schema into BigQuery with free batch load jobs.

    python -m ingestion.load_to_bigquery                   # tables that changed since the last load
    python -m ingestion.load_to_bigquery --all             # every table
    python -m ingestion.load_to_bigquery --source direct   # bootstrap: straight from the source

Fivetran can't write to a BigQuery sandbox, so it lands the source in Postgres and this step
copies each table across as it is, including Fivetran's `_fivetran_synced` and `_fivetran_deleted`
columns, plus a `_loaded_at` timestamp. Columns and types are read from Postgres on every run, so a
column Fivetran adds or retypes reaches BigQuery with the next load.

`--source direct` is a stand-in for when the Fivetran connection isn't live: it reads the source
schema `hris` and adds the two Fivetran columns itself (nothing deleted, synced = load time), so
everything downstream can be built and tested. Each BigQuery table is labelled with where its data
came from (`load_source`), and switching back to Fivetran reloads every table.

Sandbox rules it follows:
- tables are never partitioned (the sandbox drops date partitions older than 60 days);
- every load counts against a 10 GiB lifetime storage cap, so unchanged tables are skipped;
- tables expire 60 days after they're created, so a table older than 30 days is recreated rather
  than overwritten, each load sets the expiry 59 days ahead, and a table within 14 days of
  expiring is reloaded even if nothing changed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.oauth2 import service_account
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parent.parent
LANDING_SCHEMA = "raw_hris"            # Fivetran: destination prefix "raw" + source schema "hris"
SOURCE_SCHEMA = "hris"
KEEP_DAYS = 59
RELOAD_WITHIN_DAYS = 14


def bigquery_type(data_type: str, precision: int | None, scale: int | None) -> tuple[str, pa.DataType]:
    """Postgres column type -> (BigQuery type, Parquet type)."""
    if data_type in ("smallint", "integer", "bigint"):
        return "INT64", pa.int64()
    if data_type == "numeric":
        if scale is None or scale > 9 or (precision or 38) - scale > 29:
            return "BIGNUMERIC", pa.decimal256(76, 38)
        return "NUMERIC", pa.decimal128(38, 9)
    if data_type in ("real", "double precision"):
        return "FLOAT64", pa.float64()
    if data_type == "boolean":
        return "BOOL", pa.bool_()
    if data_type == "date":
        return "DATE", pa.date32()
    if data_type == "timestamp with time zone":
        return "TIMESTAMP", pa.timestamp("us", tz="UTC")
    if data_type == "timestamp without time zone":
        return "DATETIME", pa.timestamp("us")
    return "STRING", pa.string()


Column = tuple[str, str, pa.DataType]          # name, BigQuery type, Parquet type
FIVETRAN_COLUMNS: list[Column] = [("_fivetran_deleted", "BOOL", pa.bool_()),
                                  ("_fivetran_synced", "TIMESTAMP", pa.timestamp("us", tz="UTC"))]


def postgres_tables(conn: psycopg.Connection, schema: str) -> list[str]:
    rows = conn.execute("""select table_name from information_schema.tables
                           where table_schema = %s and table_type = 'BASE TABLE' and table_name not like 'fivetran\\_%%'
                           order by table_name""", (schema,)).fetchall()
    return [r[0] for r in rows]


def postgres_columns(conn: psycopg.Connection, schema: str, table: str) -> list[Column]:
    rows = conn.execute("""select column_name, data_type, numeric_precision, numeric_scale
                           from information_schema.columns where table_schema = %s and table_name = %s
                           order by ordinal_position""", (schema, table)).fetchall()
    return [(name, *bigquery_type(dtype, prec, scale)) for name, dtype, prec, scale in rows]


class Origin:
    """Where a load reads from: Fivetran's landing schema, or (bootstrap) the source directly."""

    def __init__(self, name: str):
        self.name = name
        self.direct = name == "direct"
        self.url_var = "POSTGRES_URL" if self.direct else "LANDING_POSTGRES_URL"
        self.schema = SOURCE_SCHEMA if self.direct else LANDING_SCHEMA

    def tables(self, conn: psycopg.Connection) -> list[str]:
        return postgres_tables(conn, self.schema)

    def columns(self, conn: psycopg.Connection, table: str) -> list[Column]:
        cols = postgres_columns(conn, self.schema, table)
        return cols + FIVETRAN_COLUMNS if self.direct else cols

    def state(self, conn: psycopg.Connection, table: str) -> dict[str, str]:
        """What identifies the current copy (row count, a change marker, column layout). Stored as
        labels on the BigQuery table, so an unchanged table can be skipped."""
        ident = sql.SQL("{}.{}").format(sql.Identifier(self.schema), sql.Identifier(table))
        if self.direct:
            # No _fivetran_synced at the source: fingerprint the content instead.
            n, marker = conn.execute(sql.SQL(
                "select count(*), md5(coalesce(string_agg(t::text, '|' order by t::text), '')) from {} t"
            ).format(ident)).fetchone()
            marker = marker[:16]
        else:
            n, synced = conn.execute(sql.SQL("select count(*), max(_fivetran_synced) from {}").format(ident)).fetchone()
            marker = str(int(synced.timestamp()) if synced else 0)
        layout = ",".join(f"{name}:{bq}" for name, bq, _ in self.columns(conn, table))
        return {"landing_rows": str(n), "landing_synced": marker,
                "landing_cols": hashlib.sha1(layout.encode()).hexdigest()[:16], "load_source": self.name}

    def select(self, conn: psycopg.Connection, table: str, now: datetime) -> tuple[list[Column], list[tuple]]:
        columns = self.columns(conn, table)
        stored = columns[:-2] if self.direct else columns
        # Anything stored as STRING is read as Postgres text, so jsonb, uuid, time or arrays load as text.
        select = [sql.SQL("{0}::text as {0}").format(sql.Identifier(name)) if bq == "STRING" else sql.Identifier(name)
                  for name, bq, _ in stored]
        if self.direct:
            select += [sql.SQL("false"), sql.Literal(now)]
        query = sql.SQL("select {} from {}.{}").format(
            sql.SQL(", ").join(select), sql.Identifier(self.schema), sql.Identifier(table))
        return columns, conn.execute(query).fetchall()

    def description(self, table: str) -> str:
        if self.direct:
            return (f"Bootstrap copy of source table {SOURCE_SCHEMA}.{table}, with Fivetran-style columns added; "
                    "a stand-in until the Fivetran connection is live.")
        return f"Copy of landing table {LANDING_SCHEMA}.{table} (written by Fivetran)."


def bigquery_client() -> tuple[bigquery.Client, str]:
    keyfile = os.environ["BQ_KEYFILE"]
    creds = service_account.Credentials.from_service_account_file(keyfile)
    project = os.environ["GCP_PROJECT_ID"]
    client = bigquery.Client(project=project, credentials=creds, location=os.environ["BQ_LOCATION"])
    return client, f"{project}.{os.environ['BQ_DATASET_RAW']}"


def up_to_date(client: bigquery.Client, table_id: str, state: dict[str, str], now: datetime) -> bool:
    try:
        existing = client.get_table(table_id)
    except NotFound:
        return False
    labels = existing.labels or {}
    same = all(labels.get(k) == v for k, v in state.items())
    fresh = existing.expires is not None and existing.expires - now > timedelta(days=RELOAD_WITHIN_DAYS)
    return same and fresh


def copy_table(origin: Origin, conn: psycopg.Connection, client: bigquery.Client, table: str, table_id: str,
               state: dict[str, str], now: datetime) -> int:
    columns, rows = origin.select(conn, table, now)
    arrays = [pa.array([r[i] for r in rows], type=arrow) for i, (_, _, arrow) in enumerate(columns)]
    arrays.append(pa.array([now] * len(rows), type=pa.timestamp("us", tz="UTC")))
    names = [name for name, _, _ in columns] + ["_loaded_at"]
    schema = [bigquery.SchemaField(name, bq) for name, bq, _ in columns]
    schema.append(bigquery.SchemaField("_loaded_at", "TIMESTAMP"))

    # The sandbox counts a table's 60 days from its creation, so an old table is recreated rather than
    # overwritten (same bytes written either way); a fresh table's expiry can then be set 59 days ahead.
    try:
        if client.get_table(table_id).created < now - timedelta(days=30):
            client.delete_table(table_id)
    except NotFound:
        pass

    with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
        pq.write_table(pa.Table.from_arrays(arrays, names=names), f.name)
        job_config = bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.PARQUET, schema=schema,
                                            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
        with open(f.name, "rb") as data:
            client.load_table_from_file(data, table_id, job_config=job_config).result()

    loaded = client.get_table(table_id)
    loaded.expires = now + timedelta(days=KEEP_DAYS)
    loaded.labels = state
    loaded.description = origin.description(table)
    client.update_table(loaded, ["expires", "labels", "description"])
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="reload every table, changed or not")
    ap.add_argument("--source", choices=["fivetran", "direct"], default="fivetran",
                    help="fivetran: the landing schema (default); direct: bootstrap straight from the source")
    args = ap.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    origin = Origin(args.source)
    url = os.environ.get(origin.url_var)
    if not url:
        sys.exit(f"{origin.url_var} is not set (in .env or the environment)")

    client, dataset = bigquery_client()
    now = datetime.now(UTC)
    print(f"Loading from {origin.schema} ({origin.name}) into {dataset}")
    # Autocommit: each read releases its locks at once, so a long load never blocks a Fivetran sync.
    with psycopg.connect(url, connect_timeout=15, autocommit=True) as conn:
        tables = origin.tables(conn)
        if not tables:
            sys.exit(f"Nothing in schema {origin.schema} yet: has Fivetran finished its first sync?")
        for table in tables:
            table_id = f"{dataset}.{table}"
            state = origin.state(conn, table)
            if not args.all and up_to_date(client, table_id, state, now):
                print(f"  {table:<24} unchanged, skipped")
                continue
            n = copy_table(origin, conn, client, table, table_id, state, now)
            print(f"  {table:<24} {n:>7,} rows loaded, expires {(now + timedelta(days=KEEP_DAYS)):%Y-%m-%d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
