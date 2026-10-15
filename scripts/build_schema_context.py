"""Write the context an AI agent gets about the warehouse, from dbt's own metadata.

    python scripts/build_schema_context.py          # eval/schema_context.md: documented
    python scripts/build_schema_context.py --bare   # eval/schema_bare.md: table and column names only

Reads dbt/target/manifest.json and catalog.json (run `dbt docs generate` first) and
docs/metric-definitions.md. Only the marts are described: they're what an agent should query.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "dbt" / "target"


def marts(manifest: dict) -> list[dict]:
    nodes = [n for n in manifest["nodes"].values()
             if n["resource_type"] == "model" and "/marts/" in "/" + n["original_file_path"]]
    return sorted(nodes, key=lambda n: (not n["name"].startswith("dim_"), n["name"]))


def column_types(catalog: dict, unique_id: str) -> dict[str, str]:
    node = catalog["nodes"].get(unique_id, {})
    return {name.lower(): col["type"] for name, col in node.get("columns", {}).items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bare", action="store_true", help="names and types only, no descriptions")
    args = ap.parse_args()
    manifest = json.loads((TARGET / "manifest.json").read_text())
    catalog = json.loads((TARGET / "catalog.json").read_text())

    lines = ["# Warehouse schema", ""]
    if not args.bare:
        lines += ["BigQuery dataset `analytics` (project `people-analytics-demo-2610`). Query the tables below only.",
                  "All data is synthetic. Each table states its grain, keys, joins and the traps to avoid.", ""]
    for node in marts(manifest):
        relation = f"{node['schema']}.{node['alias'] or node['name']}"
        types = column_types(catalog, node["unique_id"])
        lines.append(f"## {node['name']}")
        lines.append("")
        if not args.bare and node.get("description"):
            lines += [node["description"].strip(), ""]
        lines.append(f"Table: `{relation}`" if not args.bare else f"Table: `analytics.{node['name']}`")
        lines.append("")
        lines.append("| column | type |" + (" description |" if not args.bare else ""))
        lines.append("| --- | --- |" + (" --- |" if not args.bare else ""))
        documented = node.get("columns", {})
        for name in types or documented:
            desc = " ".join(documented.get(name, {}).get("description", "").split())
            lines.append(f"| {name} | {types.get(name, '')} |" + (f" {desc} |" if not args.bare else ""))
        lines.append("")
    if not args.bare:
        lines += ["---", "", (REPO / "docs" / "metric-definitions.md").read_text().strip(), ""]
    out = REPO / "eval" / ("schema_bare.md" if args.bare else "schema_context.md")
    out.write_text("\n".join(lines))
    print(f"wrote {out.relative_to(REPO)} ({len(marts(manifest))} tables, {len(lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
