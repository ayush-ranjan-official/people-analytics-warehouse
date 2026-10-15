"""Fail unless the Visier migration inventory is complete and every reference in it is real.

    python scripts/check_migration_inventory.py                    # checks docs/migration-inventory.csv
    python scripts/check_migration_inventory.py path/to/copy.csv

The inventory lists what a Visier tenant would hold for this data and where each item lives in the
warehouse. It is a rehearsal: Nodewell Labs is fictional, its data synthetic and its Visier tenant
hypothetical, so the Visier side of each row is Visier's documented definition, not a tenant export.

What it checks:
- the header is exactly the twelve inventory columns, and every column but lookml_field is filled in;
- item_type, priority and status use the allowed values (statuses below), and no visier_item appears twice;
- every dbt_model is a model file in dbt/models/marts or dbt/models/reporting;
- every lookml_field is a field in lookml/ (dimension groups expanded into their timeframes);
- every analytics.<table>.<column> in bigquery_target, and every column in analytics.<table> (col, col),
  is a dbt model and a column its schema file lists;
- every hris_* or ats_* table named in original_source_system is a source table in _sources.yml;
- every mart model and every LookML measure (hidden ones too) appears in at least one row.
dbt_model and lookml_field can hold several values separated by ';'. A file saved with a byte-order mark
(Excel's "CSV UTF-8") reads the same as one without.

Status values:
- migrated: built, and either it follows Visier's documented definition or no open client question would
  change the number;
- reconciling: built, but a known definition difference or an open client question can still change the
  number (or who can see it); it becomes migrated once the parallel run passes or the decision is logged
  in docs/decisions.md;
- gap: Visier has it for this data and the warehouse does not yet;
- retired: deliberately not rebuilt, with the reason in notes.
"""

import csv
import re
import sys
from pathlib import Path

import lkml
import yaml

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "docs" / "migration-inventory.csv"
MODELS = ROOT / "dbt" / "models"
LOOKML = ROOT / "lookml"

COLUMNS = ["visier_item", "item_type", "definition", "original_source_system", "bigquery_target", "grain",
           "dbt_model", "lookml_field", "owner", "priority", "status", "notes"]
OPTIONAL = {"lookml_field"}
ALLOWED = {
    "item_type": {"subject", "event", "metric", "dimension", "property", "concept"},
    "priority": {"P1", "P2", "P3"},
    "status": {"migrated", "reconciling", "retired", "gap"},
}
# Looker's default timeframes for a type: time dimension group that lists none (as validate_lookml.py).
DEFAULT_TIMEFRAMES = ["raw", "date", "day_of_month", "day_of_week", "day_of_week_index", "day_of_year", "hour",
                      "hour_of_day", "minute", "month", "month_name", "month_num", "quarter", "quarter_of_year",
                      "time", "time_of_day", "week", "week_of_year", "year"]
DEFAULT_INTERVALS = ["day", "hour", "minute", "month", "quarter", "second", "week", "year"]
TARGET_REF = re.compile(r"\banalytics\.([a-z0-9_]+)(?:\.([a-z0-9_]+))?")
# analytics.<table> (col, col): only lists of bare identifiers, so prose in parentheses is skipped.
TARGET_COLUMNS = re.compile(r"\banalytics\.([a-z0-9_]+) \(([a-z0-9_]+(?:, [a-z0-9_]+)*)\)")
SOURCE_TABLE = re.compile(r"\b(?:hris|ats)_[a-z_]+\b")


def split(value: str) -> list[str]:
    return [v.strip() for v in value.split(";") if v.strip()]


def lookml_fields() -> tuple[set[str], set[str]]:
    """Every view.field in lookml/, and the subset that are measures."""
    fields, measures = set(), set()
    for path in sorted(LOOKML.rglob("*.lkml")):
        for view in lkml.load(path.read_text()).get("views", []):
            name = view["name"]
            for kind in ("dimensions", "measures", "filters", "parameters"):
                fields |= {f"{name}.{f['name']}" for f in view.get(kind, [])}
            measures |= {f"{name}.{m['name']}" for m in view.get("measures", [])}
            for group in view.get("dimension_groups", []):
                if group.get("type", "time") == "duration":
                    expanded = [f"{i}s_{group['name']}" for i in group.get("intervals", DEFAULT_INTERVALS)]
                else:
                    expanded = [f"{group['name']}_{t}" for t in group.get("timeframes", DEFAULT_TIMEFRAMES)]
                fields |= {f"{name}.{f}" for f in expanded}
    return fields, measures


def model_columns() -> dict[str, set[str]]:
    """Columns listed in each folder's schema file, by model name (empty if a model lists none)."""
    columns = {}
    for path in MODELS.rglob("_*.yml"):
        for model in (yaml.safe_load(path.read_text()) or {}).get("models", []):
            columns[model["name"]] = {c["name"] for c in model.get("columns", [])}
    return columns


def source_tables() -> set[str]:
    tables = set()
    for source in yaml.safe_load((MODELS / "staging" / "_sources.yml").read_text())["sources"]:
        tables |= {t.get("identifier", t["name"]) for t in source["tables"]}
    return tables


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else INVENTORY
    marts = {p.stem for p in (MODELS / "marts").glob("*.sql")}
    reporting = {p.stem for p in (MODELS / "reporting").glob("*.sql")}
    all_models = {p.stem for p in MODELS.rglob("*.sql")}
    fields, measures = lookml_fields()
    columns = model_columns()
    sources = source_tables()

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        # Skip blank lines, but not a line whose only content is fields beyond the header (key None).
        rows = [(reader.line_num, row) for row in reader
                if row.get(None) or any((v or "").strip() for k, v in row.items() if k is not None)]

    problems = []
    if header != COLUMNS:
        missing = [c for c in COLUMNS if c not in header]
        extra = [c for c in header if c not in COLUMNS]
        problems.append(f"header: expected {', '.join(COLUMNS)}"
                        + (f"; missing {', '.join(missing)}" if missing else "")
                        + (f"; unexpected {', '.join(extra)}" if extra else ""))

    seen, used_models, used_fields = {}, set(), set()
    for line, row in rows:
        extra = row.pop(None, None)  # csv puts fields beyond the header here
        row = {k: (v or "").strip() for k, v in row.items()}
        where = f"line {line} ({row.get('visier_item') or '?'})"
        if extra:
            problems.append(f"{where}: {len(extra)} more field(s) than the header (an unquoted comma?)")
        for column in COLUMNS:
            if column not in OPTIONAL and not row.get(column):
                problems.append(f"{where}: {column} is empty")
        for column, allowed in ALLOWED.items():
            if row.get(column) and row[column] not in allowed:
                problems.append(f"{where}: {column} '{row[column]}' is not one of {', '.join(sorted(allowed))}")
        item = row.get("visier_item", "")
        if item in seen:
            problems.append(f"{where}: visier_item already listed on line {seen[item]}")
        seen.setdefault(item, line)

        for model in split(row.get("dbt_model", "")):
            used_models.add(model)
            if model not in marts | reporting:
                problems.append(f"{where}: dbt_model {model} is not a model in dbt/models/marts or reporting")
        for field in split(row.get("lookml_field", "")):
            used_fields.add(field)
            if field not in fields:
                problems.append(f"{where}: lookml_field {field} is not a field in lookml/")
        for table, column in TARGET_REF.findall(row.get("bigquery_target", "")):
            if table not in all_models:
                problems.append(f"{where}: bigquery_target analytics.{table} is not a dbt model")
            elif column and columns.get(table) and column not in columns[table]:
                problems.append(f"{where}: bigquery_target analytics.{table}.{column} is not a column of {table}")
        for table, listed in TARGET_COLUMNS.findall(row.get("bigquery_target", "")):
            for column in (c.strip() for c in listed.split(",")):
                if columns.get(table) and column not in columns[table]:
                    problems.append(f"{where}: bigquery_target analytics.{table}.{column} is not a column of {table}")
        for table in SOURCE_TABLE.findall(row.get("original_source_system", "")):
            if table not in sources:
                problems.append(f"{where}: original_source_system {table} is not a source table")

    for mart in sorted(marts - used_models):
        problems.append(f"mart {mart} is in no row's dbt_model")
    for measure in sorted(measures - used_fields):
        problems.append(f"LookML measure {measure} is in no row's lookml_field")

    marts_covered, measures_covered = len(marts & used_models), len(measures & used_fields)
    summary = (f"{len(rows)} rows, {marts_covered} of {len(marts)} marts and "
               f"{measures_covered} of {len(measures)} LookML measures covered")
    if not problems:
        print(f"Inventory OK: {summary}")
        return 0
    print(f"Inventory has {len(problems)} problem(s): {summary}")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
