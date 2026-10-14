"""Known data-quality problems, added on purpose after the clean data has been checked.

Rows are chosen only from history that is settled by `history_end - 30 days`, using a fixed
seed, so the same rows are planted for any cut on or after history_end.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from pathlib import Path

from generator.workforce import add_months, seeded_rng


def plant(tables: dict[str, list[dict]], cfg: dict) -> list[dict]:
    """Modify `tables` in place; return a description of every planted row."""
    pc = cfg["planted_issues"]
    if not pc["enabled"]:
        return []
    seed = int(cfg["seed"])
    settled = cfg["company"]["history_end"] - timedelta(days=30)
    planted: list[dict] = []

    # 1. "Engineering - X" keyed as "Engg - X" in the HRIS department table.
    typo_targets = [d for d in tables["hris_departments"] if d["department_name"].startswith("Engineering - ")]
    for dept in typo_targets[: pc["department_name_typos"]]:
        before = dept["department_name"]
        dept["department_name"] = before.replace("Engineering - ", "Engg - ")
        planted.append({"issue": "department_name_typo", "table": "hris_departments",
                        "key": dept["department_id"], "detail": f"'{before}' stored as '{dept['department_name']}'"})

    # 2. Applications submitted twice: same candidate, requisition, source and day; new id.
    rng = seeded_rng(seed, "planted", "duplicates")
    eligible = sorted((a for a in tables["ats_applications"] if a["applied_date"] <= settled),
                      key=lambda a: a["application_id"])
    picks = sorted(rng.choice(len(eligible), size=pc["duplicate_applications"], replace=False))
    for n, i in enumerate(picks, start=1):
        original = eligible[int(i)]
        dup = copy.copy(original)
        dup.update(application_id=f"A9{n:06d}", current_stage="applied", hired_employee_id=None,
                   stage_updated_at=_first_stage_time(tables, original))
        tables["ats_applications"].append(dup)
        planted.append({"issue": "duplicate_application", "table": "ats_applications",
                        "key": dup["application_id"], "detail": f"duplicate of {original['application_id']}"})

    # 3. Closed job records whose effective_to was keyed a year too early (ends before it starts).
    rng = seeded_rng(seed, "planted", "inverted")
    eligible = sorted((j for j in tables["hris_job_history"]
                       if j["effective_to"] <= settled and (j["effective_to"] - j["effective_from"]).days < 300),
                      key=lambda j: j["job_history_id"])
    for i in sorted(rng.choice(len(eligible), size=pc["inverted_job_history"], replace=False)):
        row = eligible[int(i)]
        before = row["effective_to"]
        row["effective_to"] = add_months(before, -12)
        planted.append({"issue": "inverted_job_history", "table": "hris_job_history", "key": row["job_history_id"],
                        "detail": f"effective_to {before} keyed as {row['effective_to']} "
                                  f"(effective_from {row['effective_from']})"})

    # 4. Exit records keyed with ids from the old HRIS, which match no employee.
    rng = seeded_rng(seed, "planted", "orphans")
    start = cfg["company"]["history_start"]
    for n in range(1, pc["orphan_terminations"] + 1):
        legacy_id = f"E0{int(rng.integers(1000, 9999))}"
        when = start + timedelta(days=int(rng.integers(0, (settled - start).days)))
        tables["hris_terminations"].append({
            "termination_id": f"T9{n:04d}", "employee_id": legacy_id, "termination_date": when,
            "termination_type": "V", "reason_code": "VOL_PERSONAL", "regretted_flag": False})
        planted.append({"issue": "orphan_termination", "table": "hris_terminations", "key": f"T9{n:04d}",
                        "detail": f"employee_id {legacy_id} does not exist"})
    return planted


def _first_stage_time(tables: dict[str, list[dict]], application: dict) -> datetime:
    for s in tables["ats_application_stages"]:
        if s["application_id"] == application["application_id"] and s["stage"] == "applied":
            return s["entered_at"]
    return application["stage_updated_at"]


def write_report(planted: list[dict], path: Path, as_of: date) -> None:
    lines = ["# Planted data-quality issues", "",
             "These rows are wrong on purpose, so the warehouse tests have real problems to catch.",
             "They are written by `generator/planted_issues.py` and switched off with",
             "`planted_issues.enabled: false` in `generator/config.yaml`.", "",
             f"Generated for data as of {as_of}.", "",
             "| Issue | Table | Row | Detail |", "| --- | --- | --- | --- |"]
    lines += [f"| {p['issue']} | `{p['table']}` | `{p['key']}` | {p['detail']} |" for p in planted]
    path.write_text("\n".join(lines) + "\n")
