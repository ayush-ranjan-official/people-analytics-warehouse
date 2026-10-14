"""Check the loaded source data: row counts, FTE headcount at the end of the history, and that
every planted data-quality issue is present (or absent, if planting is switched off).
"""

from __future__ import annotations

import sys

from generator.generate import COLUMNS, load_config
from generator.load_to_postgres import connect, count_rows, get_state

CHECKS = {
    "department_name_typos": "select count(*) from hris.hris_departments where department_name like 'Engg %'",
    "duplicate_applications": """
        select coalesce(sum(n - 1), 0) from (
            select count(*) as n from hris.ats_applications
            group by req_id, candidate_id having count(*) > 1) d""",
    "inverted_job_history": "select count(*) from hris.hris_job_history where effective_to < effective_from",
    "orphan_terminations": """
        select count(*) from hris.hris_terminations t
        where not exists (select 1 from hris.hris_employees e where e.employee_id = t.employee_id)""",
}


def main() -> int:
    cfg = load_config()
    end = cfg["company"]["history_end"]
    failures = 0
    with connect() as conn:
        business_date = get_state(conn, "business_date")
        if business_date is None:
            sys.exit("The source hasn't been loaded: run `make load` first")
        print(f"Source database, business date {business_date}")
        print("Row counts:")
        for table in COLUMNS:
            print(f"  {table:<24} {count_rows(conn, table):>7,}")

        by_type = conn.execute("""
            select employment_type, count(*) from hris.hris_employees
            where hire_date <= %(d)s and (termination_date is null or termination_date >= %(d)s)
            group by 1 order by 1""", {"d": end}).fetchall()
        employed = dict(by_type)
        fte = employed.get("FTE", 0)
        target = cfg["company"]["target_fte_at_end"]
        ok = abs(fte - target) <= 0.05 * target
        failures += not ok
        print(f"\nEmployed on {end}: " + ", ".join(f"{k} {v}" for k, v in employed.items()))
        print(f"  active FTEs {fte} vs target ~{target}: {'OK' if ok else 'FAIL'}")

        planted = cfg["planted_issues"]
        expect_on = planted["enabled"]
        print("\nPlanted data-quality issues:")
        for name, query in CHECKS.items():
            found = conn.execute(query).fetchone()[0]
            expected = planted[name] if expect_on else 0
            ok = found == expected
            failures += not ok
            print(f"  {name:<24} found {found}, expected {expected}: {'OK' if ok else 'FAIL'}")

    print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
