"""Fail unless every mart model and every one of its columns has a description.

Reads dbt/target/manifest.json and catalog.json (run `dbt docs generate` first), so it also catches
columns that exist in BigQuery but were never documented.
"""

import json
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "dbt" / "target"


def main() -> int:
    manifest = json.loads((TARGET / "manifest.json").read_text())
    catalog = json.loads((TARGET / "catalog.json").read_text())
    missing, models, columns = [], 0, 0
    for node in manifest["nodes"].values():
        if node["resource_type"] != "model" or "/marts/" not in "/" + node["original_file_path"]:
            continue
        models += 1
        if not node.get("description", "").strip():
            missing.append(f"{node['name']}: no model description")
        documented = {k.lower(): v for k, v in node.get("columns", {}).items()}
        actual = catalog["nodes"].get(node["unique_id"], {}).get("columns", {})
        for name in actual:
            columns += 1
            if not documented.get(name.lower(), {}).get("description", "").strip():
                missing.append(f"{node['name']}.{name.lower()}: no description")
    print(f"{models} mart models, {columns} columns: "
          f"{models * 1 + columns - len(missing)} of {models + columns} documented")
    for m in missing:
        print(f"  {m}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
