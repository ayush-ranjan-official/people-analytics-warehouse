"""Markdown summary of the last dbt invocation and source freshness, for the GitHub job page."""

import json
import sys
from collections import Counter
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "dbt" / "target"


def main() -> int:
    print("## Nightly pipeline\n")
    results = TARGET / "run_results.json"
    if results.exists():
        runs = json.loads(results.read_text())["results"]
        counts = Counter(r["status"] for r in runs)
        print("| status | count |\n| --- | --- |")
        for status, n in sorted(counts.items()):
            print(f"| {status} | {n} |")
        failing = [r for r in runs if r["status"] in ("fail", "error")]
        if failing:
            print("\n**Failing:**\n")
            for r in failing:
                print(f"- `{r['unique_id'].split('.', 2)[-1]}`: {(r.get('message') or '').strip()[:200]}")
    freshness = TARGET / "sources.json"
    if freshness.exists():
        print("\n### Source freshness\n\n| source | status | age (hours) |\n| --- | --- | --- |")
        for r in json.loads(freshness.read_text())["results"]:
            age = r.get("max_loaded_at_time_ago_in_s")
            print(f"| `{r['unique_id'].split('.', 2)[-1]}` | {r['status']} | {age / 3600:.1f} |" if age is not None
                  else f"| `{r['unique_id'].split('.', 2)[-1]}` | {r['status']} | - |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
