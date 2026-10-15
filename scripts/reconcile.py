"""Reconcile five P1 people metrics between the source HR system and the warehouse, at two levels.

    python scripts/reconcile.py              # both levels
    python scripts/reconcile.py --level 1    # same definitions only (pipeline correctness)
    python scripts/reconcile.py --level 2    # Visier's definitions only (parallel-run rehearsal)

Exits 1 if any row FAILs, or if a period isn't closed in the warehouse yet.

Nodewell Labs is fictional and so is its Visier tenant. This rehearses a migration off Visier: the
warehouse plays the target, and the "Visier-style" figures are Visier's documented definitions applied
to the source data. None of them was exported from Visier. docs/reconciliation.md explains each
definition, its source page and its assumptions.

Metrics: Headcount, Average Headcount, Turnover (attrition) Rate, Resignation (voluntary) Rate and Time
to Hire, for closed periods only: Q3 2026 and calendar 2025, and every month-end from December 2024 to
September 2026 for headcount. The warehouse is loaded at the start of each nightly run, so it can be a
day behind the source; "today" is never compared.

Level 1, same definitions. Each metric is recomputed from the source Postgres (schema hris, read-only
queries) exactly as docs/metric-definitions.md defines it, in plain SQL and Python with no dbt and no
BigQuery, and compared with the same metric from the BigQuery marts. Counts must match exactly, rates
within 0.05 percentage points, average headcount within 0.05 and time to hire within 0.05 days.

Level 2, Visier's definitions. The same metrics, as Visier documents them, are computed from the source
and compared with the warehouse metric. Tolerances: headcount exact, rates +/-0.5 percentage points,
average headcount and time to hire within 2% of the warehouse value. The warehouse value is restated
one documented definition difference at a time, on its own marts, and each step's effect is printed;
what is left (the residual) is a data difference. A row FAILs if its residual is outside the Level 1
tolerance, whatever the size of the difference; otherwise it PASSes within tolerance and is EXPLAINED
outside it. Level 1 covers full-time staff only, so for contractors and interns the residual is the
only check.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Callable, TypeVar

import psycopg
import requests
from dotenv import load_dotenv
from google.api_core.exceptions import InternalServerError, ServiceUnavailable
from google.auth.exceptions import TransportError
from google.cloud import bigquery

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from ingestion.load_to_bigquery import bigquery_client  # noqa: E402

MAX_BYTES = 1024**3        # maximum_bytes_billed per BigQuery query; each one bills well under 100 MiB
QUERY_TIMEOUT = 180        # seconds
ATTEMPTS = 4               # network errors are retried, with a growing pause
RETRYABLE = (psycopg.OperationalError, requests.exceptions.ConnectionError, TransportError,
             ServiceUnavailable, InternalServerError, TimeoutError)

FTE = ("fte",)
ALL_TYPES = ("fte", "contractor", "intern")
EXIT_CODES = ("V", "I", "R")   # voluntary, involuntary, retirement (hris_terminations.termination_type)


@dataclass(frozen=True)
class Period:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def instants(self) -> list[date]:
        """Month-end instants for the trapezoid: the one before the period opens, then each month's."""
        return month_ends(self.start - timedelta(days=1), self.end)


def month_ends(first: date, last: date) -> list[date]:
    """Every month-end from first's month to last's, inclusive."""
    out, year, month = [], first.year, first.month
    while (year, month) <= (last.year, last.month):
        next_first = date(year + month // 12, month % 12 + 1, 1)
        out.append(next_first - timedelta(days=1))
        year, month = next_first.year, next_first.month
    return out


PERIODS = [Period("Q3 2026", date(2026, 7, 1), date(2026, 9, 30)),
           Period("2025", date(2025, 1, 1), date(2025, 12, 31))]
HEADCOUNT_INSTANTS = month_ends(date(2024, 12, 31), date(2026, 9, 30))
INSTANTS = sorted(set(HEADCOUNT_INSTANTS).union(*(p.instants() for p in PERIODS)))


# ------------------------------------------------------------------ the two sides' data, normalised

@dataclass(frozen=True)
class Exit:
    last_day: date               # termination_date: the last day employed
    code: str                    # V, I or R
    employment_type: str | None  # None when no employee record matches


@dataclass(frozen=True)
class Start:
    """An employment record that started, with the application it was hired through."""
    employment_type: str
    hire_date: date
    applied_date: date
    accepted_on: date            # the day the offer was accepted (UTC)


@dataclass
class Side:
    """What the Visier definitions need, from one system. The same functions read both sides."""
    headcount: dict[date, Counter] = field(default_factory=dict)   # instant -> employment type -> count
    exits: list[Exit] = field(default_factory=list)
    starts: list[Start] = field(default_factory=list)


def headcount(side: Side, instant: date, types: tuple[str, ...]) -> int:
    return sum(side.headcount.get(instant, Counter())[t] for t in types)


def trapezoid(counts: list[int]) -> float:
    """Average over n intervals from n + 1 instants: (h0/2 + h1 + ... + h(n-1) + hn/2) / n."""
    return (counts[0] / 2 + sum(counts[1:-1]) + counts[-1] / 2) / (len(counts) - 1)


def average_headcount(side: Side, period: Period, types: tuple[str, ...], equal_weights: bool = False) -> float:
    counts = [headcount(side, i, types) for i in period.instants()]
    return mean(counts) if equal_weights else trapezoid(counts)


def exit_count(side: Side, period: Period, codes: tuple[str, ...], types: tuple[str, ...],
               next_day: bool = True) -> int:
    """Exits in the period. next_day: an exit counts in the period that holds the day after the last day."""
    shift = timedelta(days=1 if next_day else 0)
    return sum(1 for x in side.exits
               if x.code in codes and x.employment_type in types and period.start <= x.last_day + shift <= period.end)


def time_to_hire(side: Side, period: Period, types: tuple[str, ...],
                 average: Callable = mean) -> tuple[float | None, int]:
    """Days from applying to accepting the offer, for records that started in the period."""
    days = [(s.accepted_on - s.applied_date).days for s in side.starts
            if s.employment_type in types and period.start <= s.hire_date <= period.end]
    return (average(days) if days else None), len(days)


# ------------------------------------------------------------------ source (Postgres, read-only)

SOURCE_SQL = {
    "as_of": """
        select greatest(
            (select max(hire_date) from hris.hris_employees where hire_date <= current_date),
            (select max(termination_date) from hris.hris_employees where termination_date <= current_date),
            (select max(effective_from) from hris.hris_job_history where effective_from <= current_date))""",
    "employees": """
        select trim(employee_id), lower(trim(employment_type)), hire_date, termination_date
        from hris.hris_employees""",
    "exits": """
        select t.termination_date, upper(trim(t.termination_type)), lower(trim(e.employment_type))
        from hris.hris_terminations as t
        left join hris.hris_employees as e on trim(e.employee_id) = trim(t.employee_id)""",
    # Applications whose offer was accepted: when (the first 'hired' stage, as a UTC date, the same as
    # BigQuery's date()), and whether it is the candidate's first application to that requisition.
    "accepted_offers": """
        with applications as (
            select trim(application_id) as application_id, trim(req_id) as req_id, applied_date,
                   trim(hired_employee_id) as hired_employee_id,
                   row_number() over (partition by trim(req_id), trim(candidate_id)
                                      order by trim(application_id)) = 1 as is_first
            from hris.ats_applications
        ),
        acceptances as (
            select trim(application_id) as application_id, min(entered_at) as accepted_at
            from hris.ats_application_stages
            where lower(trim(stage)) = 'hired'
            group by trim(application_id)
        )
        select a.is_first, lower(trim(r.employment_type)), a.applied_date,
               (acceptances.accepted_at at time zone 'UTC')::date, a.hired_employee_id
        from applications as a
        join acceptances using (application_id)
        left join hris.ats_requisitions as r on trim(r.req_id) = a.req_id""",
}

T = TypeVar("T")


def with_retries(call: Callable[[], T]) -> T:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return call()
        except RETRYABLE as e:
            if attempt == ATTEMPTS:
                raise
            print(f"  network error ({type(e).__name__}), retrying in {5 * attempt} s", file=sys.stderr)
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def read_source() -> dict[str, list[tuple]]:
    def fetch() -> dict[str, list[tuple]]:
        with psycopg.connect(os.environ["POSTGRES_URL"], connect_timeout=20, autocommit=True) as conn:
            conn.execute("set default_transaction_read_only = on")
            return {name: conn.execute(query).fetchall() for name, query in SOURCE_SQL.items()}
    return with_retries(fetch)


@dataclass
class Source:
    as_of: date
    employees: list[tuple[str, str, date, date | None]]
    side: Side
    fte_offers: list[tuple[date, date]]   # (applied_date, accepted_on), FTE requisitions, first application


def build_source(raw: dict[str, list[tuple]]) -> Source:
    employees = raw["employees"]
    side = Side()
    for instant in INSTANTS:
        side.headcount[instant] = Counter(
            etype for _, etype, hired, left in employees if hired <= instant and (left is None or instant <= left))
    side.exits = [Exit(last_day, code, etype) for last_day, code, etype in raw["exits"]]
    by_id = {eid: (etype, hired) for eid, etype, hired, _ in employees}
    for _, _, applied, accepted, hired_employee_id in raw["accepted_offers"]:
        if hired_employee_id in by_id:
            etype, hired = by_id[hired_employee_id]
            side.starts.append(Start(etype, hired, applied, accepted))
    fte_offers = [(applied, accepted) for is_first, req_type, applied, accepted, _ in raw["accepted_offers"]
                  if is_first and req_type == "fte"]
    return Source(raw["as_of"][0][0], employees, side, fte_offers)


def source_daily_average(employees: list[tuple], period: Period) -> float:
    """The warehouse definition from the source: FTEs employed on each day, averaged over every day."""
    fte = [(hired, left) for _, etype, hired, left in employees if etype == "fte"]
    person_days = 0
    for offset in range(period.days):
        day = period.start + timedelta(days=offset)
        person_days += sum(1 for hired, left in fte if hired <= day and (left is None or day <= left))
    return person_days / period.days


# ------------------------------------------------------------------ warehouse (BigQuery marts)

def periods_cte() -> str:
    rows = ",\n            ".join(
        f"struct('{p.name}' as period, date '{p.start}' as start_date, date '{p.end}' as end_date)" for p in PERIODS)
    return f"select * from unnest([\n            {rows}])"


WAREHOUSE_SQL = {
    "reporting_date": "select reporting_date from analytics.int_reporting_date",
    # Headcount on each instant as docs/metric-definitions.md counts it (countif(is_fte)), and the other
    # employment types for the Visier restatement.
    "headcount": """
        select snapshot_date,
               countif(is_fte) as fte,
               countif(employment_type = 'contractor') as contractor,
               countif(employment_type = 'intern') as intern
        from analytics.fct_headcount_daily
        where snapshot_date in unnest(@instants)
        group by snapshot_date""",
    # Average headcount, exits and time to hire per period, as docs/metric-definitions.md defines them.
    "period_metrics": f"""
        with periods as (
            {periods_cte()}
        ),
        period_headcount as (
            select p.period, count(*) / count(distinct h.snapshot_date) as average_headcount
            from periods as p
            join analytics.fct_headcount_daily as h on h.snapshot_date between p.start_date and p.end_date
            where h.is_fte
            group by p.period
        ),
        period_exits as (
            select p.period,
                   countif(t.termination_date between p.start_date and p.end_date) as exit_count,
                   countif(t.termination_date between p.start_date and p.end_date
                           and t.termination_type = 'voluntary') as voluntary_count
            from periods as p
            cross join analytics.fct_terminations as t
            group by p.period
        ),
        period_time_to_hire as (
            select p.period,
                   avg(if(date(f.hired_at) between p.start_date and p.end_date,
                          date_diff(date(f.hired_at), f.applied_date, day), null)) as average_days,
                   countif(date(f.hired_at) between p.start_date and p.end_date) as offers
            from periods as p
            cross join analytics.fct_application_funnel as f
            where f.employment_type = 'fte' and f.reached_hired
            group by p.period
        )
        select h.period, h.average_headcount, e.exit_count, e.voluntary_count, t.average_days, t.offers
        from period_headcount as h
        join period_exits as e using (period)
        join period_time_to_hire as t using (period)""",
    # Every exit, for the Visier restatement. FTE exits come from the mart. Contractor and intern exits
    # are left out of fct_terminations on purpose, so they come from staging, typed by dim_employee.
    "exits": """
        select termination_date, termination_type_code, 'fte' as employment_type
        from analytics.fct_terminations
        union all
        select t.termination_date, t.termination_type_code, e.employment_type
        from analytics.stg_hris__terminations as t
        join (select distinct employee_id, employment_type from analytics.dim_employee) as e using (employee_id)
        where e.employment_type != 'fte'""",
    # Every start with the application it was hired through, for the Visier restatement of time to hire.
    "starts": """
        select e.employment_type, e.hire_date, f.applied_date, date(f.hired_at) as accepted_on
        from (select distinct employee_id, employment_type, hire_date from analytics.dim_employee) as e
        join analytics.fct_application_funnel as f on f.hired_employee_id = e.employee_id
        where f.reached_hired""",
}


def query(client: bigquery.Client, sql: str, params: list | None = None) -> list[bigquery.Row]:
    config = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES, query_parameters=params or [])
    return with_retries(lambda: list(client.query(sql, job_config=config).result(timeout=QUERY_TIMEOUT)))


@dataclass
class Warehouse:
    reporting_date: date
    side: Side
    period_metrics: dict[str, bigquery.Row]


def read_warehouse(client: bigquery.Client) -> Warehouse:
    reporting = query(client, WAREHOUSE_SQL["reporting_date"])[0]["reporting_date"]
    side = Side()
    instants = bigquery.ArrayQueryParameter("instants", "DATE", INSTANTS)
    for row in query(client, WAREHOUSE_SQL["headcount"], [instants]):
        side.headcount[row["snapshot_date"]] = Counter(
            fte=row["fte"], contractor=row["contractor"], intern=row["intern"])
    metrics = {row["period"]: row for row in query(client, WAREHOUSE_SQL["period_metrics"])}
    side.exits = [Exit(r["termination_date"], r["termination_type_code"], r["employment_type"])
                  for r in query(client, WAREHOUSE_SQL["exits"])]
    side.starts = [Start(r["employment_type"], r["hire_date"], r["applied_date"], r["accepted_on"])
                   for r in query(client, WAREHOUSE_SQL["starts"])]
    return Warehouse(reporting, side, metrics)


# ------------------------------------------------------------------ comparison rows and formatting

UNITS = {   # decimals, suffix of a value, suffix of a difference
    "count": (0, "", ""),
    "average": (2, "", ""),
    "rate": (2, "%", " pp"),
    "days": (2, " d", " d"),
}


def shown_value(v: float, unit: str) -> str:
    decimals, suffix, _ = UNITS[unit]
    return f"{round(v, 6):,.{decimals}f}{suffix}"   # round(.., 6): 26.225 shows the same from both systems


def shown_difference(d: float, unit: str) -> str:
    decimals, _, suffix = UNITS[unit]
    d = round(d, decimals)
    return (f"{d:+,.{decimals}f}" if d else f"{0:.{decimals}f}") + suffix


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


@dataclass
class Row:
    metric: str
    period: str
    unit: str
    left: float                  # Level 1: the source; Level 2: the warehouse
    right: float                 # Level 1: the warehouse; Level 2: Visier-style, from the source
    tolerance: float             # absolute, in the metric's unit
    left_note: str = ""
    right_note: str = ""
    counts_match: bool = True    # Level 1: numerators (exits, offers) must also match exactly
    steps: list[tuple[str, float]] = field(default_factory=list)   # Level 2: (step, value after it)
    status: str = ""

    @property
    def difference(self) -> float:
        return self.right - self.left

    def shown(self, v: float, note: str = "") -> str:
        return shown_value(v, self.unit) + (f" ({note})" if note else "")

    def shown_difference(self, d: float) -> str:
        return shown_difference(d, self.unit)

    def shown_tolerance(self) -> str:
        return "exact" if self.tolerance == 0 else "+/-" + shown_difference(self.tolerance, self.unit).lstrip("+")


# Level 1's tolerance, also what a Level 2 residual must be within for the difference to count as explained.
LIKE_FOR_LIKE = {"count": 0, "average": 0.05, "rate": 0.05, "days": 0.05}


def within(d: float, tolerance: float) -> bool:
    return abs(d) <= tolerance + 1e-9


def print_table(title: str, headers: tuple[str, str], rows: list[Row], show_tolerance: bool) -> None:
    print(f"\n{title}")
    widths = (26, 10, 22, 22, 12, 12)
    cols = ["metric", "period", headers[0], headers[1], "difference"] + (["tolerance"] if show_tolerance else [])
    print("  " + " ".join(f"{c:<{w}}" for c, w in zip(cols, widths)) + "status")
    for r in rows:
        cells = [r.metric, r.period, r.shown(r.left, r.left_note), r.shown(r.right, r.right_note),
                 r.shown_difference(r.difference)]
        if show_tolerance:
            cells.append(r.shown_tolerance())
        print("  " + " ".join(f"{c:<{w}}" for c, w in zip(cells, widths)) + r.status)


# ------------------------------------------------------------------ Level 1: same definitions

def level_1(src: Source, wh: Warehouse) -> list[Row]:
    rows = []
    for instant in HEADCOUNT_INSTANTS:
        rows.append(Row("Headcount (FTE)", f"{instant}", "count",
                        headcount(src.side, instant, FTE), headcount(wh.side, instant, FTE), LIKE_FOR_LIKE["count"]))
    for p in PERIODS:
        m = wh.period_metrics[p.name]
        src_avg = source_daily_average(src.employees, p)
        src_exits = exit_count(src.side, p, EXIT_CODES, FTE, next_day=False)
        src_voluntary = exit_count(src.side, p, ("V",), FTE, next_day=False)
        offers = [(accepted - applied).days for applied, accepted in src.fte_offers if p.start <= accepted <= p.end]
        rows += [
            Row("Average headcount (FTE)", p.name, "average", src_avg, m["average_headcount"],
                LIKE_FOR_LIKE["average"]),
            Row("Attrition rate", p.name, "rate", 100 * src_exits / src_avg,
                100 * m["exit_count"] / m["average_headcount"], LIKE_FOR_LIKE["rate"],
                f"{src_exits} exits", f"{m['exit_count']} exits", src_exits == m["exit_count"]),
            Row("Voluntary attrition rate", p.name, "rate", 100 * src_voluntary / src_avg,
                100 * m["voluntary_count"] / m["average_headcount"], LIKE_FOR_LIKE["rate"],
                f"{src_voluntary} exits", f"{m['voluntary_count']} exits", src_voluntary == m["voluntary_count"]),
            Row("Time to hire (mean)", p.name, "days", mean(offers) if offers else float("nan"),
                m["average_days"] if m["average_days"] is not None else float("nan"), LIKE_FOR_LIKE["days"],
                f"{len(offers)} offers", f"{m['offers']} offers", len(offers) == m["offers"]),
        ]
    for r in rows:
        r.status = "PASS" if within(r.difference, r.tolerance) and r.counts_match else "FAIL"
    return rows


# ------------------------------------------------------------------ Level 2: Visier's definitions

RATE_METRICS = [   # Visier metric, the exit codes it counts
    ("Turnover Rate", EXIT_CODES),
    ("Resignation Rate", ("V",)),
    ("Voluntary Turnover Rate", ("V", "R")),   # compared with voluntary attrition too: the naming trap
]
NON_FTE = (("contractor", "contractors"), ("intern", "interns"))


def non_fte_mix(count: Callable[[tuple[str, ...]], int]) -> str:
    return ", ".join(f"{count((t,))} {plural_name}" for t, plural_name in NON_FTE)


def level_2(src: Source, wh: Warehouse) -> list[Row]:
    rows = []
    s, w = src.side, wh.side
    for p in PERIODS:
        m = wh.period_metrics[p.name]
        daily = m["average_headcount"]
        trap_fte, trap_all = average_headcount(w, p, FTE), average_headcount(w, p, ALL_TYPES)
        visier_average = average_headcount(s, p, ALL_TYPES)

        # Headcount (employeeCount): active employees of every type at the period's last instant
        rows.append(Row("Headcount", f"{p.end}", "count", headcount(w, p.end, FTE), headcount(s, p.end, ALL_TYPES), 0,
                        steps=[(f"contractors and interns counted ({non_fte_mix(lambda t: headcount(w, p.end, t))})",
                                headcount(w, p.end, ALL_TYPES))]))

        # Average Headcount: trapezoid rule over month-end instants
        instants = p.instants()
        rows.append(Row("Average Headcount", p.name, "average", daily, visier_average, 0.02 * daily, steps=[
            (f"FTEs averaged over the {len(instants)} month-ends {instants[0]} to {instants[-1]} by the trapezoid "
             f"rule, not over {p.days} days", trap_fte),
            ("contractors and interns counted", trap_all)]))

        # Turnover, Resignation and Voluntary Turnover Rates: exits / Average Headcount. Each step adds
        # the exits it names to the warehouse count, so a data difference can only show in the residual.
        for name, codes in RATE_METRICS:
            warehouse_codes = EXIT_CODES if codes == EXIT_CODES else ("V",)
            exits = m["exit_count"] if codes == EXIT_CODES else m["voluntary_count"]
            steps = [(f"denominator: FTE Average Headcount by the trapezoid rule ({trap_fte:.2f}, not {daily:.2f})",
                      100 * exits / trap_fte)]
            extra = tuple(c for c in codes if c not in warehouse_codes)
            if extra:
                n = exit_count(w, p, extra, FTE, next_day=False)
                exits += n
                steps.append((f"retirements counted as voluntary (+{plural(n, 'exit')})", 100 * exits / trap_fte))
            fte_exits = [x for x in w.exits if x.code in codes and x.employment_type == "fte"]
            moved_in = sum(1 for x in fte_exits if x.last_day == p.start - timedelta(days=1))
            moved_out = sum(1 for x in fte_exits if x.last_day == p.end)
            exits += moved_in - moved_out
            steps.append((f"exits counted in the period of the day after the last day employed "
                          f"({moved_in} moved in, {moved_out} moved out)", 100 * exits / trap_fte))
            added = {t: exit_count(w, p, codes, (t,)) for t, _ in NON_FTE}
            exits += sum(added.values())
            mix = f": {non_fte_mix(lambda t: added[t[0]])}" if sum(added.values()) else ""
            steps.append((f"contractors and interns counted (+{plural(sum(added.values()), 'exit')}{mix}; "
                          f"denominator {trap_fte:.2f} -> {trap_all:.2f})", 100 * exits / trap_all))
            numerator = m["exit_count"] if codes == EXIT_CODES else m["voluntary_count"]
            visier_exits = exit_count(s, p, codes, ALL_TYPES)
            rows.append(Row(name, p.name, "rate", 100 * numerator / daily, 100 * visier_exits / visier_average, 0.5,
                            plural(numerator, "exit"), plural(visier_exits, "exit"), steps=steps))

        # Time to Hire: applied to offer accepted, a property of the Employment Start event
        nan = float("nan")   # a period with no starts or offers can't be compared: the row fails
        visier_days, visier_n = time_to_hire(s, p, ALL_TYPES)
        fte_days, fte_n = time_to_hire(w, p, FTE)
        all_days, _ = time_to_hire(w, p, ALL_TYPES)
        visier_days, fte_days, all_days = (nan if d is None else d for d in (visier_days, fte_days, all_days))
        warehouse_days = nan if m["average_days"] is None else m["average_days"]
        rows.append(Row("Time to Hire", p.name, "days", warehouse_days, visier_days, 0.02 * warehouse_days,
                        plural(m["offers"], "offer"), plural(visier_n, "start"), steps=[
                            (f"FTEs counted by start date, not offer-accepted date ({plural(m['offers'], 'offer')} "
                             f"accepted, {plural(fte_n, 'start')})", fte_days),
                            (f"contractors and interns counted ({non_fte_mix(lambda t: time_to_hire(w, p, t)[1])})",
                             all_days)]))

    for r in rows:
        if not within(r.right - r.steps[-1][1], LIKE_FOR_LIKE[r.unit]):
            r.status = "FAIL"        # a data difference, whether or not the total is within tolerance
        elif within(r.difference, r.tolerance):
            r.status = "PASS"
        else:
            r.status = "EXPLAINED"
    return rows


def print_explanations(rows: list[Row]) -> None:
    print("\n  Each difference, step by step: the warehouse value restated on its own marts, one definition at a time.")
    print("  Residual = Visier-style value minus the fully restated warehouse value: a data problem unless it is 0.")
    for r in rows:
        print(f"\n  {r.metric}, {r.period}: {r.shown(r.left)} -> {r.shown(r.right)} "
              f"({r.shown_difference(r.difference)})  {r.status}")
        value = r.left
        for label, after in r.steps:
            print(f"    {r.shown_difference(after - value):>11}  {label}")
            value = after
        print(f"    {r.shown_difference(r.right - value):>11}  residual")


def show_days(value: float | None, places: int = 2) -> str:
    """Days for display, or n/a for a period with no matching starts."""
    return "n/a" if value is None else f"{value:.{places}f} d"


def print_assumptions(src: Source) -> None:
    """The Visier-style inputs, and the effect of each detail that Visier's public docs leave open."""
    s = src.side
    print("\nVisier-style inputs and assumptions (from the source)")
    for p in PERIODS:
        counts = ", ".join(f"{i:%m-%d} {headcount(s, i, ALL_TYPES)}" for i in p.instants())
        print(f"  {p.name}, Headcount at month-ends {p.instants()[0]:%Y-%m-%d} onwards: {counts}")
    for p in PERIODS:
        trap, equal = average_headcount(s, p, ALL_TYPES), average_headcount(s, p, ALL_TYPES, equal_weights=True)
        exits = exit_count(s, p, EXIT_CODES, ALL_TYPES)
        print(f"  {p.name}, month-end weights: trapezoid {trap:.2f} (Turnover Rate {100 * exits / trap:.2f}%), "
              f"equal {equal:.2f} ({100 * exits / equal:.2f}%)")
    for p in PERIODS:
        print(f"  {p.name}, exit period: {exit_count(s, p, EXIT_CODES, ALL_TYPES)} exits by the day after the last day "
              f"employed, {exit_count(s, p, EXIT_CODES, ALL_TYPES, next_day=False)} by the last day")
    for p in PERIODS:   # a tenant that maps termination_date as the first day not employed
        counts = [sum(1 for _, _, hired, left in src.employees if hired <= i and (left is None or i < left))
                  for i in p.instants()]
        exits = exit_count(s, p, EXIT_CODES, ALL_TYPES, next_day=False)
        print(f"  {p.name}, termination date as the first day not employed: Average Headcount "
              f"{trapezoid(counts):.2f}, Turnover Rate {100 * exits / trapezoid(counts):.2f}%")
    first, last = HEADCOUNT_INSTANTS[0], HEADCOUNT_INSTANTS[-1]
    on_month_ends = Counter(x.employment_type for x in s.exits
                            if (x.last_day + timedelta(days=1)).day == 1 and first <= x.last_day <= last)
    print(f"  Exits dated on a month's last day, {first:%Y-%m} to {last:%Y-%m}, which move to the next month: "
          + ", ".join(plural(on_month_ends[t], noun) for t, noun in zip(ALL_TYPES, ("FTE", "contractor", "intern"))))
    for p in PERIODS:
        fte_average = average_headcount(s, p, FTE)
        print(f"  {p.name}, contingent workers excluded (FTEs only): Headcount {headcount(s, p.end, FTE)}, "
              f"Average Headcount {fte_average:.2f}, "
              f"Turnover Rate {100 * exit_count(s, p, EXIT_CODES, FTE) / fte_average:.2f}%, "
              f"Resignation Rate {100 * exit_count(s, p, ('V',), FTE) / fte_average:.2f}%, "
              f"Time to Hire {show_days(time_to_hire(s, p, FTE)[0])}")
    for p in PERIODS:
        print(f"  {p.name}, Time to Hire: mean {show_days(time_to_hire(s, p, ALL_TYPES)[0])}, "
              f"median {show_days(time_to_hire(s, p, ALL_TYPES, average=median)[0], 1)}")


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--level", type=int, choices=[1, 2], help="run one level only")
    args = ap.parse_args()
    load_dotenv(REPO_ROOT / ".env")

    client, _ = bigquery_client()
    wh = read_warehouse(client)
    src = build_source(read_source())
    print(f"Source data to {src.as_of}; warehouse reporting date {wh.reporting_date}.")
    open_periods = [p.name for p in PERIODS if wh.reporting_date <= p.end]
    if open_periods:
        print(f"Not closed in the warehouse yet: {', '.join(open_periods)}. Run after the nightly build "
              "that loads the day after the period ends.")
        return 1

    failed = 0
    if args.level in (None, 1):
        rows = level_1(src, wh)
        print_table("Level 1: same definitions (docs/metric-definitions.md), source recomputed vs warehouse marts",
                    ("source", "warehouse"), rows, show_tolerance=False)
        failed += sum(r.status == "FAIL" for r in rows)
        print(f"  {Counter(r.status for r in rows)['PASS']} of {len(rows)} PASS. Tolerance: counts exact, "
              "average headcount 0.05, rates 0.05 pp, time to hire 0.05 days.")
    if args.level in (None, 2):
        rows = level_2(src, wh)
        print_table("Level 2: Visier's definitions emulated from the source vs the warehouse metric",
                    ("warehouse", "visier-style"), rows, show_tolerance=True)
        print("  Tolerance: headcount exact, rates 0.5 pp, average headcount and time to hire 2% of the warehouse "
              "value.")
        print_explanations(rows)
        print_assumptions(src)
        failed += sum(r.status == "FAIL" for r in rows)
        tally = Counter(r.status for r in rows)
        print(f"\n  Level 2: {tally['PASS']} PASS, {tally['EXPLAINED']} EXPLAINED, {tally['FAIL']} FAIL.")

    print(f"\n{'FAILED: ' + str(failed) + ' row(s).' if failed else 'No failures.'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
