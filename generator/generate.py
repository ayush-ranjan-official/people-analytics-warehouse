"""Generate the Nodewell Labs HRIS and ATS tables.

    python -m generator.generate                      # history up to history_end, written to CSV
    python -m generator.generate --as-of 2026-10-05   # the data as it would look on a later date

Before the planted issues are added, the clean data is checked for consistency: job history runs
from the hire date to the exit date with no gaps or overlaps, managers are employed for the rows
that point at them, no seat has two occupants, every termination matches its employee, every hire
since history_start came through a filled requisition, and ATS stages move forward in time.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path

import faker
import numpy
import yaml

from generator.planted_issues import plant, write_report
from generator.recruiting import STAGES, Application, Recruiting, materialize_ats
from generator.workforce import OPEN_END, Workforce

HERE = Path(__file__).resolve().parent

# Column order of every source table; the loader and schema.sql use the same order.
COLUMNS = {
    "hris_departments": ["department_id", "department_name", "parent_department_id", "cost_center"],
    "hris_positions": ["position_id", "job_title", "department_id", "job_level", "is_filled", "created_at",
                       "closed_date"],
    "hris_employees": ["employee_id", "first_name", "last_name", "email", "hire_date", "termination_date",
                       "status", "employment_type", "updated_at"],
    "hris_job_history": ["job_history_id", "employee_id", "effective_from", "effective_to", "position_id",
                         "department_id", "manager_id", "location", "job_level", "salary_local", "currency",
                         "change_reason"],
    "hris_terminations": ["termination_id", "employee_id", "termination_date", "termination_type",
                          "reason_code", "regretted_flag"],
    "ats_requisitions": ["req_id", "position_id", "department_id", "hiring_manager_id", "opened_date",
                         "closed_date", "status", "employment_type"],
    "ats_applications": ["application_id", "req_id", "candidate_id", "source", "applied_date",
                         "current_stage", "stage_updated_at", "hired_employee_id"],
    "ats_application_stages": ["stage_event_id", "application_id", "stage", "entered_at"],
}
PRIMARY_KEYS = {table: cols[0] for table, cols in COLUMNS.items()}


def load_config(path: Path = HERE / "config.yaml") -> dict:
    return yaml.safe_load(path.read_text())


def fingerprint(cfg: dict) -> str:
    """Everything that decides the simulated history, apart from whether issues are planted."""
    core = {k: v for k, v in cfg.items() if k != "planted_issues"}
    blob = json.dumps(core, sort_keys=True, default=str) + numpy.__version__ + faker.VERSION
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def simulate(cfg: dict, as_of: date) -> tuple[Workforce, list[Application]]:
    wf = Workforce(cfg).run(as_of)
    return wf, Recruiting(wf).build()


def tables_as_of(cfg: dict, wf: Workforce, apps: list[Application], as_of: date,
                 check: bool = True) -> tuple[dict[str, list[dict]], list[dict]]:
    tables = wf.materialize(as_of)
    tables.update(materialize_ats(apps, as_of))
    if check:
        check_rules(tables, cfg, as_of)
    planted = plant(tables, cfg)
    return tables, planted


def check_rules(t: dict[str, list[dict]], cfg: dict, as_of: date) -> None:
    """Exit with the problems found if the clean data is internally inconsistent."""
    problems: list[str] = []
    employees = {e["employee_id"]: e for e in t["hris_employees"]}

    for e in employees.values():
        if (e["status"] == "Terminated") != (e["termination_date"] is not None):
            problems.append(f"{e['employee_id']}: status {e['status']} vs termination_date {e['termination_date']}")

    jobs = defaultdict(list)
    for j in t["hris_job_history"]:
        jobs[j["employee_id"]].append(j)
    for emp_id, rows in jobs.items():
        rows.sort(key=lambda j: j["effective_from"])
        for prev, nxt in pairwise(rows):
            if (nxt["effective_from"] - prev["effective_to"]).days != 1:
                problems.append(f"{emp_id}: gap or overlap after {prev['job_history_id']}")
        for j in rows:
            if j["effective_to"] < j["effective_from"]:
                problems.append(f"{j['job_history_id']}: ends before it starts")
        end = employees[emp_id]["termination_date"] or OPEN_END
        if rows[-1]["effective_to"] != end:
            problems.append(f"{emp_id}: last job row ends {rows[-1]['effective_to']}, expected {end}")
        if rows[0]["effective_from"] != employees[emp_id]["hire_date"]:
            problems.append(f"{emp_id}: first job row doesn't start on the hire date")
    for emp_id in employees.keys() - jobs.keys():
        problems.append(f"{emp_id}: no job history")

    # A manager is employed for the whole of every row that points at them.
    for j in t["hris_job_history"]:
        mgr = employees.get(j["manager_id"]) if j["manager_id"] else None
        if j["manager_id"] and mgr is None:
            problems.append(f"{j['job_history_id']}: manager {j['manager_id']} doesn't exist")
        elif mgr and (mgr["hire_date"] > j["effective_from"] or
                      (mgr["termination_date"] and mgr["termination_date"] < min(j["effective_to"], as_of))):
            problems.append(f"{j['job_history_id']}: manager {j['manager_id']} not employed for the whole row")

    # Managers are never junior to the people they manage (compared on the row's first day).
    level_on = defaultdict(list)
    for j in t["hris_job_history"]:
        level_on[j["employee_id"]].append((j["effective_from"], int(j["job_level"][1:])))
    for rows in level_on.values():
        rows.sort()

    def level_at(emp_id: str, d: date) -> int | None:
        levels = [lv for start, lv in level_on.get(emp_id, []) if start <= d]
        return levels[-1] if levels else None

    for j in t["hris_job_history"]:
        mgr_level = level_at(j["manager_id"], j["effective_from"]) if j["manager_id"] else None
        if mgr_level is not None and mgr_level < int(j["job_level"][1:]):
            problems.append(f"{j['job_history_id']}: manager {j['manager_id']} is junior to {j['employee_id']}")

    # Requisitions name a hiring manager who is employed when the requisition opens.
    for r in t["ats_requisitions"]:
        mgr = employees.get(r["hiring_manager_id"])
        if mgr is None or mgr["hire_date"] > r["opened_date"] or (
                mgr["termination_date"] and mgr["termination_date"] < r["opened_date"]):
            problems.append(f"{r['req_id']}: hiring manager {r['hiring_manager_id']} not employed when it opened")

    # A seat is either filled, closed, or being recruited for (an open requisition, or a filled
    # one whose new hire hasn't started yet).
    reqs_for = defaultdict(list)
    for r in t["ats_requisitions"]:
        reqs_for[r["position_id"]].append(r)
    starts_in = defaultdict(list)
    for j in t["hris_job_history"]:
        if j["change_reason"] in ("Hire", "Rehire"):
            starts_in[j["position_id"]].append(j["effective_from"])
    for p in t["hris_positions"]:
        if p["is_filled"] and p["closed_date"]:
            problems.append(f"{p['position_id']}: closed but filled")
        elif not p["is_filled"] and not p["closed_date"] and not any(
                r["status"] == "Open" or (r["status"] == "Filled"
                                          and not any(s >= r["opened_date"] for s in starts_in[p["position_id"]]))
                for r in reqs_for[p["position_id"]]):
            problems.append(f"{p['position_id']}: empty, not closed, and nobody is recruiting for it")

    # One person per seat at a time.
    seats = defaultdict(list)
    for j in t["hris_job_history"]:
        seats[j["position_id"]].append(j)
    for pos_id, rows in seats.items():
        rows.sort(key=lambda j: j["effective_from"])
        for prev, nxt in pairwise(rows):
            if prev["employee_id"] != nxt["employee_id"] and nxt["effective_from"] <= prev["effective_to"]:
                problems.append(f"{pos_id}: held by {prev['employee_id']} and {nxt['employee_id']} at once")

    for term in t["hris_terminations"]:
        emp = employees.get(term["employee_id"])
        if emp is None or emp["termination_date"] != term["termination_date"]:
            problems.append(f"{term['termination_id']}: doesn't match its employee")

    # Every hire inside the history window came through a requisition that reached 'hired'.
    hired = {a["hired_employee_id"]: a for a in t["ats_applications"] if a["hired_employee_id"]}
    filled = {r["req_id"] for r in t["ats_requisitions"] if r["status"] == "Filled"}
    for e in employees.values():
        if e["hire_date"] >= cfg["company"]["history_start"]:
            app = hired.get(e["employee_id"])
            if app is None or app["current_stage"] != "hired" or app["req_id"] not in filled:
                problems.append(f"{e['employee_id']}: hired without a filled requisition")

    # ATS: one hire per filled requisition, nobody applies before it opens or progresses after it
    # closes, one application per candidate per requisition, and stages move forward in time.
    reqs = {r["req_id"]: r for r in t["ats_requisitions"]}
    hires = Counter(a["req_id"] for a in t["ats_applications"] if a["current_stage"] == "hired")
    for r in reqs.values():
        expected_hires = 1 if r["status"] == "Filled" else 0
        if hires[r["req_id"]] != expected_hires:
            problems.append(f"{r['req_id']}: {hires[r['req_id']]} hires on a {r['status']} requisition")
    app_req = {a["application_id"]: reqs[a["req_id"]] for a in t["ats_applications"]}
    for a in t["ats_applications"]:
        if a["applied_date"] < reqs[a["req_id"]]["opened_date"]:
            problems.append(f"{a['application_id']}: applied before the requisition opened")
    for s in t["ats_application_stages"]:
        r = app_req[s["application_id"]]
        closed = r["closed_date"]
        if s["stage"] in ("screen", "interview", "offer") and closed and s["entered_at"].date() > closed:
            problems.append(f"{s['application_id']}: {s['stage']} after the requisition closed")

    # ATS: one application per candidate per requisition, and stages move forward in time.
    pairs = Counter((a["req_id"], a["candidate_id"]) for a in t["ats_applications"])
    problems += [f"{req}/{cand}: {n} applications" for (req, cand), n in pairs.items() if n > 1]
    last_seen: dict[str, datetime] = {}
    for s in sorted(t["ats_application_stages"],
                    key=lambda s: (s["application_id"], int(s["stage_event_id"].rsplit("-", 1)[1]))):
        prev = last_seen.get(s["application_id"])
        if prev and s["entered_at"] < prev:
            problems.append(f"{s['application_id']}: stage times go backwards")
        last_seen[s["application_id"]] = s["entered_at"]

    if problems:
        sample = "\n  ".join(problems[:20])
        raise SystemExit(f"Generated data breaks {len(problems)} rule(s):\n  {sample}")


def funnel_summary(t: dict[str, list[dict]]) -> str:
    reached = Counter(s["stage"] for s in t["ats_application_stages"])
    applied = reached["applied"] or 1
    parts = [f"{stage} {reached[stage] / applied * 100:.1f}" for stage in STAGES]
    return "per 100 applied: " + " -> ".join(parts)


def to_csv_value(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    return v


def write_csvs(tables: dict[str, list[dict]], out: Path) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    for leftover in [*out.glob("*.csv"), *out.glob("*.txt")]:
        leftover.unlink()
    checksums = {}
    for table, cols in COLUMNS.items():
        path = out / f"{table}.csv"
        rows = sorted(tables[table], key=lambda r: r[cols[0]])
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([to_csv_value(r[c]) for c in cols])
        checksums[table] = hashlib.sha256(path.read_bytes()).hexdigest()
    return checksums


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the synthetic HRIS + ATS tables.")
    ap.add_argument("--as-of", type=date.fromisoformat, help="cut the data at this date (default: history_end)")
    ap.add_argument("--out", type=Path, default=HERE / "output")
    args = ap.parse_args()

    cfg = load_config()
    as_of = args.as_of or cfg["company"]["history_end"]
    if as_of < cfg["company"]["history_end"]:
        ap.error(f"--as-of must be on or after history_end ({cfg['company']['history_end']})")
    wf, apps = simulate(cfg, as_of)
    tables, planted = tables_as_of(cfg, wf, apps, as_of)
    checksums = write_csvs(tables, args.out)
    (args.out / "meta.json").write_text(json.dumps(
        {"as_of": as_of.isoformat(), "seed": cfg["seed"], "fingerprint": fingerprint(cfg),
         "rows": {t: len(r) for t, r in tables.items()}, "sha256": checksums}, indent=2) + "\n")
    if planted and as_of == cfg["company"]["history_end"]:
        write_report(planted, HERE / "PLANTED_ISSUES.md", as_of)

    print(f"{cfg['company']['name']} data as of {as_of} -> {args.out}")
    for table in COLUMNS:
        print(f"  {table:<24} {len(tables[table]):>7,} rows")
    print(f"  {funnel_summary(tables)}")
    print(f"  planted issues: {len(planted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
