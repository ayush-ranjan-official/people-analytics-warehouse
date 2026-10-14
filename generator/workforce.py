"""Day-by-day simulation of the Nodewell Labs workforce.

The simulation runs from the opening population on `history_start` up to an `as_of` date. Every
random draw comes from a generator seeded by what it's about (a day, a requisition, a person), so
simulating one more day never changes what already happened. `--simulate-day` relies on that.

Rows record their true timeline (for example the real end of a job record). `materialize()`
turns that into what the HRIS shows on a given date.

Org rules, applied the same way before and during the history window:
- the most senior FTE in a department is its head (the CEO is never a department head);
- a head reports to the head of the parent department, or to the CEO;
- everyone else reports to an L5+ manager senior to them in their department, or to its head.
"""

from __future__ import annotations

import calendar
import functools
import math
import unicodedata
import zlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
from faker import Faker

OPEN_END = date(9999, 12, 31)


def seeded_rng(seed: int, *key: object) -> np.random.Generator:
    """A generator that depends only on the seed and the key, never on draw order elsewhere."""
    return np.random.default_rng([seed, *(zlib.crc32(str(k).encode()) for k in key)])


def pick(rng: np.random.Generator, items: Sequence):
    return items[int(rng.integers(len(items)))]


def add_months(d: date, months: int) -> date:
    year, month = divmod(d.month - 1 + months, 12)
    year, month = d.year + year, month + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


def next_monday(d: date) -> date:
    """d itself if it's a Monday, otherwise the following Monday."""
    return d + timedelta(days=(7 - d.weekday()) % 7)


def first_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def last_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def daily_rate(annual: float) -> float:
    return -math.log(1 - annual) / 365.25


def normalised(weights: Sequence[float]) -> np.ndarray:
    w = np.array(weights, dtype=float)
    return w / w.sum()


@dataclass
class Job:
    job_history_id: str
    employee_id: str
    effective_from: date
    position_id: str
    department_id: str
    manager_id: str | None
    location: str
    job_level: int
    salary_local: int
    currency: str
    change_reason: str
    effective_to: date | None = None      # true last day; None while still current


@dataclass
class Employee:
    employee_id: str
    person_id: int
    first_name: str
    last_name: str
    email: str
    hire_date: date
    employment_type: str                  # FTE / Contractor / Intern
    level_since: date
    job: Job | None = None                # current job row, set right after creation
    planned_end: date | None = None       # contract or internship end
    termination_date: date | None = None
    name_changes: list[tuple[date, str, str]] = field(default_factory=list)


@dataclass
class Stint:
    start: date
    end: date | None = None               # last day in the seat; None while occupied


@dataclass
class Position:
    position_id: str
    department_id: str
    created_at: date
    grades: list[tuple[date, int, str]]   # (from, level, title): a promotion regrades the seat in place
    occupied: list[Stint] = field(default_factory=list)
    closed_date: date | None = None       # seat eliminated (vacated and not backfilled)


@dataclass
class Requisition:
    req_id: str
    position_id: str
    department_id: str
    hiring_manager_id: str | None
    job_level: int
    location: str
    employment_type: str
    opened_date: date
    closed_date: date                     # offer accepted, or cancelled
    outcome: str                          # Filled / Cancelled
    hire_date: date | None
    hired_employee_id: str | None = None


@dataclass
class Termination:
    termination_id: str
    employee_id: str
    termination_date: date
    termination_type: str
    reason_code: str
    regretted_flag: bool | None


class Workforce:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.seed = int(cfg["seed"])
        co = cfg["company"]
        self.start: date = co["history_start"]
        self.end: date = co["history_end"]
        self.founded: date = co["founded"]
        self.domain = co["email_domain"]

        self.departments = {d["id"]: d for d in cfg["departments"]}
        self.leaf_departments = [d["id"] for d in cfg["departments"] if d["weight"] > 0]
        self.dept_weights = normalised([self.departments[d]["weight"] for d in self.leaf_departments])

        self.levels = {int(k[1:]): v for k, v in cfg["levels"].items()}
        self.level_ids = sorted(self.levels)
        self.level_shares = normalised([self.levels[lv]["share"] for lv in self.level_ids])

        self.locations = cfg["locations"]
        self.location_names = list(self.locations)
        self.location_shares = normalised([self.locations[n]["share"] for n in self.location_names])

        att = cfg["attrition"]
        season = att["monthly_seasonality"]
        mean_season = sum(season.values()) / 12
        self.seasonality = {int(m): v / mean_season for m, v in season.items()}
        self.p_vol = daily_rate(att["voluntary_annual_base"])
        self.p_invol = daily_rate(att["involuntary_annual"])
        self.p_retire = daily_rate(att["retirement_annual"])
        # Rough overall exit rate for headcount planning (the first-year effect adds ~20%).
        self.expected_exit_rate = (att["voluntary_annual_base"] * 1.2 + att["involuntary_annual"]
                                   + att["retirement_annual"])
        mv = cfg["movement"]
        self.p_transfer = daily_rate(mv["transfer_annual"])
        self.p_manager_change = daily_rate(mv["manager_change_annual"])
        self.p_name_change = daily_rate(mv["name_change_annual"])

        self.fakers = {loc["faker_locale"]: Faker(loc["faker_locale"]) for loc in self.locations.values()}

        self.employees: dict[str, Employee] = {}
        self.positions: dict[str, Position] = {}
        self.requisitions: dict[str, Requisition] = {}
        self.terminations: list[Termination] = []
        self.jobs: list[Job] = []
        self.emails: set[str] = set()
        self.reports: dict[str, set[str]] = {}
        self.dept_members: dict[str, set[str]] = {d: set() for d in self.departments}
        self.last_adjusted: dict[str, date] = {}
        self._pending_backfills: list[tuple[Position, int, str]] = []
        self.ceo_id: str | None = None
        self._seq = {"E": 10000, "P": 10000, "R": 10000, "J": 0, "T": 0, "person": 0}
        self.simulated_to: date | None = None

    # ------------------------------------------------------------------ helpers
    def _next(self, kind: str) -> int:
        self._seq[kind] += 1
        return self._seq[kind]

    def rng(self, *key: object) -> np.random.Generator:
        return seeded_rng(self.seed, *key)

    def title(self, dept_id: str, level: int) -> str:
        dept = self.departments[dept_id]
        if "head_title" in dept:
            return dept["head_title"] if level >= 7 else f"Head of {dept['name']}"
        prefix = self.levels[level]["title"]
        if level >= 5:
            return f"{prefix}, {dept['name']}"
        return f"{prefix} {self.cfg['roles'][dept_id]}".strip()

    def band_mid(self, level: int, location: str) -> float:
        loc = self.locations[location]
        return self.levels[level]["base_usd"] * loc["pay"] * loc["fx"]

    def pay_step(self, location: str) -> int:
        return 1000 if self.locations[location]["currency"] == "INR" else 100

    def band_floor(self, level: int, location: str) -> int:
        """Lowest salary in the band, on the pay step."""
        step = self.pay_step(location)
        return math.ceil(self.band_mid(level, location) * (1 - self.cfg["band_spread"]) / step - 1e-6) * step

    def band_max(self, level: int, location: str) -> int:
        """Highest salary in the band, on the pay step."""
        step = self.pay_step(location)
        return math.floor(self.band_mid(level, location) * (1 + self.cfg["band_spread"]) / step + 1e-6) * step

    def round_pay(self, amount: float, location: str) -> int:
        step = self.pay_step(location)
        return int(round(amount / step) * step)

    def salary(self, level: int, location: str, rng: np.random.Generator) -> int:
        spread = self.cfg["band_spread"]
        drawn = self.round_pay(self.band_mid(level, location) * rng.uniform(1 - spread, 1 + spread), location)
        return min(max(drawn, self.band_floor(level, location)), self.band_max(level, location))

    def compa_ratio(self, emp: Employee) -> float:
        """Salary relative to the midpoint of the person's band (1.0 = midpoint)."""
        return emp.job.salary_local / self.band_mid(emp.job.job_level, emp.job.location)

    def active(self, emp: Employee, d: date) -> bool:
        return emp.hire_date <= d and (emp.termination_date is None or emp.termination_date >= d)

    def active_ids(self, d: date, employment_type: str | None = None) -> list[str]:
        return sorted(e.employee_id for e in self.employees.values()
                      if self.active(e, d) and (employment_type is None or e.employment_type == employment_type))

    def managers_above(self, dept_id: str, d: date, level: int, exclude: tuple[str | None, ...] = ()) -> list[str]:
        """Active FTE people managers (L5+) in a department, senior to `level`. Never the CEO."""
        return sorted(e for e in self.dept_members[dept_id]
                      if e not in exclude and e != self.ceo_id and self.employees[e].employment_type == "FTE"
                      and self.active(self.employees[e], d) and self.employees[e].termination_date != d
                      and self.employees[e].job.job_level >= 5 and self.employees[e].job.job_level > level)

    def new_person(self, location: str, rng: np.random.Generator) -> tuple[int, str, str, str]:
        person_id = self._next("person")
        fake = self.fakers[self.locations[location]["faker_locale"]]
        fake.seed_instance(int(rng.integers(0, 2**31)))
        first, last = fake.first_name(), fake.last_name()
        return person_id, first, last, self.unique_email(first, last)

    def unique_email(self, first: str, last: str) -> str:
        def ascii_part(s: str) -> str:
            s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
            return "".join(ch for ch in s.lower() if ch.isalnum())
        local = f"{ascii_part(first)}.{ascii_part(last)}"
        email, n = f"{local}@{self.domain}", 1
        while email in self.emails:
            n += 1
            email = f"{local}{n}@{self.domain}"
        self.emails.add(email)
        return email

    # ------------------------------------------------------------------ positions
    def new_position(self, dept_id: str, level: int, created: date) -> Position:
        pos = Position(f"P{self._next('P')}", dept_id, created, [(created, level, self.title(dept_id, level))])
        self.positions[pos.position_id] = pos
        return pos

    def regrade(self, pos: Position, d: date, level: int) -> None:
        pos.grades.append((d, level, self.title(pos.department_id, level)))

    def vacate(self, pos: Position, last_day: date, backfill: bool, location: str) -> None:
        """The seat is empty after `last_day`: re-open it with a requisition, or close it the next day."""
        pos.occupied[-1].end = last_day
        if backfill:
            self._pending_backfills.append((pos, pos.grades[-1][1], location))
        else:
            pos.closed_date = last_day + timedelta(days=1)

    # ------------------------------------------------------------------ org structure
    def head(self, dept_id: str | None, d: date, exclude: str | None = None) -> str | None:
        """Most senior active FTE in a department; on a tie, whoever reached that level first. Never the CEO."""
        if dept_id is None:
            return self.ceo_id
        members = [self.employees[e] for e in self.dept_members[dept_id]
                   if e not in (exclude, self.ceo_id) and self.employees[e].employment_type == "FTE"
                   and self.active(self.employees[e], d)]
        if not members:
            return None
        return min(members, key=lambda e: (-e.job.job_level, e.level_since, e.hire_date, e.employee_id)).employee_id

    def boss_of_head(self, dept_id: str, d: date) -> str | None:
        parent = self.departments[dept_id]["parent"]
        return (self.head(parent, d) if parent else None) or self.ceo_id

    def expected_manager(self, emp: Employee, d: date) -> str | None:
        if emp.employee_id == self.ceo_id:
            return None
        dept = emp.job.department_id
        if self.head(dept, d) == emp.employee_id:
            return self.boss_of_head(dept, d)
        managers = self.managers_above(dept, d, emp.job.job_level, exclude=(emp.employee_id,))
        if managers:
            return min(managers, key=lambda m: (len(self.reports.get(m, ())), m))
        return self.head(dept, d, exclude=emp.employee_id) or self.ceo_id

    def manager_valid(self, emp: Employee, d: date, heads: dict[str, str | None]) -> bool:
        """A head reports outside their department; everyone else to someone senior inside it."""
        mgr_id = emp.job.manager_id
        if emp.employee_id == self.ceo_id:
            return mgr_id is None
        if mgr_id is None or mgr_id not in self.employees or mgr_id == emp.employee_id:
            return False
        mgr = self.employees[mgr_id]
        if not self.active(mgr, d) or mgr.termination_date == d:
            return False
        dept = emp.job.department_id
        if heads[dept] == emp.employee_id or heads[dept] is None:
            return mgr_id == self.boss_of_head(dept, d) or mgr_id == self.ceo_id
        if mgr_id == self.ceo_id or mgr.job.department_id != dept:
            return False
        return heads[dept] == mgr_id or (mgr.job.job_level >= 5 and mgr.job.job_level > emp.job.job_level)

    def set_manager(self, emp: Employee, mgr_id: str | None) -> None:
        old = emp.job.manager_id
        if old:
            self.reports.get(old, set()).discard(emp.employee_id)
        if mgr_id:
            self.reports.setdefault(mgr_id, set()).add(emp.employee_id)

    # ------------------------------------------------------------------ job history
    def start_job(self, emp: Employee, d: date, reason: str, *, position: Position, level: int,
                  location: str, salary: int) -> Job:
        job = Job(f"JH{self._next('J'):06d}", emp.employee_id, d, position.position_id, position.department_id,
                  None, location, level, salary, self.locations[location]["currency"], reason)
        self.jobs.append(job)
        emp.job = job
        position.occupied.append(Stint(d))
        self.dept_members[position.department_id].add(emp.employee_id)
        return job

    def change(self, emp: Employee, d: date, reason: str, **updates) -> None:
        """Close the current job row and open a new one from day d (or amend today's row)."""
        old = emp.job
        if "department_id" in updates and updates["department_id"] != old.department_id:
            self.dept_members[old.department_id].discard(emp.employee_id)
            self.dept_members[updates["department_id"]].add(emp.employee_id)
        if "manager_id" in updates:
            self.set_manager(emp, updates["manager_id"])
        if old.effective_from == d:          # several changes on one day become one row
            for k, v in updates.items():
                setattr(old, k, v)
            return
        old.effective_to = d - timedelta(days=1)
        fields = {k: getattr(old, k) for k in ("position_id", "department_id", "manager_id", "location",
                                               "job_level", "salary_local", "currency")}
        fields.update(updates)
        job = Job(f"JH{self._next('J'):06d}", emp.employee_id, d, change_reason=reason, **fields)
        self.jobs.append(job)
        emp.job = job

    def promote(self, emp: Employee, d: date, salary: int) -> None:
        level = emp.job.job_level + 1
        self.regrade(self.positions[emp.job.position_id], d, level)
        self.change(emp, d, "Promotion", job_level=level, salary_local=salary)
        emp.level_since = d

    # ------------------------------------------------------------------ opening population
    def build_opening_population(self) -> None:
        day0 = self.start - timedelta(days=1)
        n_fte = int(self.cfg["company"]["initial_fte"])
        self._hire_opening_ftes(day0, n_fte)
        self._hire_opening_contractors(day0, n_fte)
        self._set_opening_managers(day0)

    def _hire_opening_ftes(self, day0: date, n_fte: int) -> None:
        """Leadership first (CEO, the VP of the parent department, a head per team), then L1-L5 staff."""
        rng = self.rng("opening")
        parents = [d for d, dept in self.departments.items() if dept["weight"] == 0]
        plan = [(self.cfg["company"]["ceo_department"], 7)] + [(d, 7) for d in parents]
        plan += [(d, 6 if self.departments[d]["weight"] >= 0.08 else 5) for d in self.leaf_departments]
        remaining = n_fte - len(plan)
        depts = rng.choice(self.leaf_departments, size=remaining, p=self.dept_weights)
        lvls = rng.choice(self.level_ids[:5], size=remaining, p=normalised(self.level_shares[:5]))
        plan += [(str(dept), int(lvl)) for dept, lvl in zip(depts, lvls, strict=True)]

        span = (day0 - self.founded).days
        people = []
        for i, (dept, level) in enumerate(plan):
            prng = self.rng("opening-person", i)
            if i == 0:
                hire = self.founded
            else:
                # Tenure is roughly exponential but can't reach back past the founding, so draw it
                # truncated at the company's age instead of piling longer draws onto day one.
                scale = (1.6 + 0.45 * level) * 365
                tenure = int(-scale * math.log(1 - prng.random() * (1 - math.exp(-span / scale))))
                hire = max(self.founded, monday_on_or_before(day0 - timedelta(days=tenure)))
            location = str(prng.choice(self.location_names, p=self.location_shares))
            people.append((hire, i, dept, level, location, prng))
        people.sort(key=lambda p: (p[0], p[1]))

        for hire, i, dept, level, location, prng in people:
            emp = self._create_employee(hire, "FTE", location, prng)
            if i == 0:
                self.ceo_id = emp.employee_id
            self._replay_opening_career(emp, i, dept, level, location, day0, prng)

    def _replay_opening_career(self, emp: Employee, i: int, dept: str, level: int, location: str,
                               day0: date, prng: np.random.Generator) -> None:
        """Pre-history for someone already employed on day0: the April merit reviews since they
        joined, and possibly one promotion on a 1 Apr / 1 Oct cycle date. Pay is worked out
        backwards from today's salary, then replayed forwards inside each level's band."""
        mv = self.cfg["movement"]
        hire = emp.hire_date
        promoted_on = None
        cycle_days = [date(y, m, 1) for y in range(hire.year, day0.year + 1) for m in mv["promotion_cycle_months"]
                      if add_months(hire, 12) <= date(y, m, 1) <= day0 - timedelta(days=30)]
        if i > 0 and level >= 2 and cycle_days and prng.random() < 0.5:
            promoted_on = pick(prng, cycle_days)
        merit_days = [date(y, mv["merit_month"], 1) for y in range(hire.year, day0.year + 1)]
        merit_days = [m for m in merit_days if (m - hire).days >= 180 and m <= day0 and m != promoted_on]
        events = [(m, "Merit Increase") for m in merit_days]
        if promoted_on:
            events = sorted([*events, (promoted_on, "Promotion")])
        raises = {when: prng.uniform(*(mv["promotion_raise"] if kind == "Promotion" else mv["merit_raise"]))
                  for when, kind in events}

        first_level = level - 1 if promoted_on else level
        pay = self.salary(level, location, prng)
        for when, _ in reversed(events):
            pay /= 1 + raises[when]
        pay = min(max(self.round_pay(pay, location), self.band_floor(first_level, location)),
                  self.band_max(first_level, location))

        pos = self.new_position(dept, first_level, hire)
        if i == 0:
            pos.grades[0] = (hire, level, self.cfg["company"]["ceo_title"])
        self.start_job(emp, hire, "Hire", position=pos, level=first_level, location=location, salary=pay)
        for when, kind in events:
            current = emp.job.job_level + (1 if kind == "Promotion" else 0)
            pay = min(max(self.round_pay(pay * (1 + raises[when]), location), self.band_floor(current, location)),
                      self.band_max(current, location))
            if kind == "Promotion":
                self.regrade(pos, when, current)
                self.change(emp, when, kind, job_level=current, salary_local=pay)
                emp.level_since = when
            elif pay > emp.job.salary_local:
                self.change(emp, when, kind, salary_local=pay)

    def _hire_opening_contractors(self, day0: date, n_fte: int) -> None:
        """Contractors already on site, with staggered contract ends."""
        con = self.cfg["contractors"]
        for i in range(round(n_fte * self.cfg["company"]["contractor_share_of_fte"])):
            prng = self.rng("opening-contractor", i)
            hire = day0 - timedelta(days=int(prng.integers(30, 300)))
            dept = str(prng.choice(self.leaf_departments, p=self.dept_weights))
            level = int(prng.choice(con["levels"]))
            location = str(prng.choice(self.location_names, p=self.location_shares))
            emp = self._create_employee(hire, "Contractor", location, prng)
            emp.planned_end = add_months(hire, int(prng.integers(*con["contract_months"])))
            if emp.planned_end < self.start:
                emp.planned_end = self.start + timedelta(days=int(prng.integers(20, 200)))
            self.start_job(emp, hire, "Hire", position=self.new_position(dept, level, hire), level=level,
                           location=location, salary=self.salary(level, location, prng))

    def _set_opening_managers(self, day0: date) -> None:
        """Replay the org rules over the opening population's history, from the founding to day0.

        Nobody in the opening population moves department, so a person's manager can only change
        when someone in their department (or its parent) joins or is promoted. At each of those
        dates the manager is re-derived with the same rules, and the row is split with a
        "Manager Change" if the answer moved.
        """
        rows_of: dict[str, list[Job]] = defaultdict(list)
        for job in self.jobs:
            rows_of[job.employee_id].append(job)
        dept_of = {e: rows[0].department_id for e, rows in rows_of.items()}
        fte_in: dict[str, list[str]] = defaultdict(list)
        for e in sorted(rows_of):
            if self.employees[e].employment_type == "FTE" and e != self.ceo_id:
                fte_in[dept_of[e]].append(e)

        @functools.cache
        def level_on(e: str, d: date) -> int:
            return next(j.job_level for j in reversed(rows_of[e]) if j.effective_from <= d)

        @functools.cache
        def level_since_on(e: str, d: date) -> date:
            rows = [j for j in rows_of[e] if j.effective_from <= d]
            return next(j.effective_from for j in rows if j.job_level == rows[-1].job_level)

        @functools.cache
        def head_on(dept: str | None, d: date) -> str | None:
            if dept is None:
                return None
            here = [e for e in fte_in[dept] if self.employees[e].hire_date <= d]
            if not here:
                return None
            return min(here, key=lambda e: (-level_on(e, d), level_since_on(e, d), self.employees[e].hire_date, e))

        def boss_of_head_on(dept: str, d: date) -> str:
            return head_on(self.departments[dept]["parent"], d) or self.ceo_id

        def choose(e: str, d: date) -> str:
            """Rendezvous hashing over the eligible managers: teams stay balanced, and when a new
            manager appears only about 1/n of people move to them."""
            dept = dept_of[e]
            head = head_on(dept, d)
            if head in (e, None):
                return boss_of_head_on(dept, d)
            managers = [m for m in fte_in[dept] if m != e and self.employees[m].hire_date <= d
                        and level_on(m, d) >= 5 and level_on(m, d) > level_on(e, d)]
            if not managers:
                return head
            return max(managers, key=lambda m: zlib.crc32(f"{e}:{m}".encode()))

        def valid(e: str, m: str | None, d: date) -> bool:
            return m is not None and m == choose(e, d)

        rebuilt: list[Job] = []
        for e in sorted(rows_of):
            if e == self.ceo_id:
                rebuilt += rows_of[e]
                continue
            dept = dept_of[e]
            parent = self.departments[dept]["parent"]
            watched = fte_in[dept] + (fte_in[parent] if parent else [])
            change_days = sorted({j.effective_from for w in watched for j in rows_of[w]}
                                 | {j.effective_from for j in rows_of[e]})
            current_mgr = None
            for job in rows_of[e]:
                row = job
                last_day = job.effective_to or day0
                for d in (c for c in change_days if job.effective_from <= c <= last_day):
                    if not valid(e, current_mgr, d):
                        current_mgr = choose(e, d)
                        if d > row.effective_from:
                            split = Job("", e, d, row.position_id, row.department_id, current_mgr, row.location,
                                        row.job_level, row.salary_local, row.currency, "Manager Change",
                                        row.effective_to)
                            row.effective_to = d - timedelta(days=1)
                            rebuilt.append(row)
                            if self.employees[e].job is row:
                                self.employees[e].job = split
                            row = split
                    if d == row.effective_from:
                        row.manager_id = current_mgr
                rebuilt.append(row)
            self.set_manager(self.employees[e], self.employees[e].job.manager_id)
        rebuilt.sort(key=lambda j: (j.employee_id, j.effective_from))
        for n, job in enumerate(rebuilt, start=1):
            job.job_history_id = f"JH{n:06d}"
        self.jobs = rebuilt
        self._seq["J"] = len(rebuilt)

    def _create_employee(self, hire: date, employment_type: str, location: str,
                         rng: np.random.Generator, rehire_of: Employee | None = None) -> Employee:
        emp_id = f"E{self._next('E')}"
        if rehire_of:
            person_id, first, last, email = (rehire_of.person_id, rehire_of.first_name,
                                             rehire_of.last_name, rehire_of.email)
            for _, new_last, new_email in rehire_of.name_changes:
                last, email = new_last, new_email
        else:
            person_id, first, last, email = self.new_person(location, rng)
        emp = Employee(emp_id, person_id, first, last, email, hire, employment_type, level_since=hire)
        self.employees[emp_id] = emp
        return emp

    # ------------------------------------------------------------------ requisitions
    def target_fte(self, d: date) -> float:
        co = self.cfg["company"]
        a, b = co["initial_fte"], co["target_fte_at_end"]
        if d <= self.end:
            frac = (d - self.start).days / (self.end - self.start).days
            return a + (b - a) * frac
        years = (d - self.end).days / 365.25
        return b * (1 + co["growth_after_end_annual"]) ** years

    def hiring_manager(self, dept_id: str, level: int, d: date) -> str | None:
        """Who the new hire will report to: an L5+ manager senior to the role, else the head (who keeps
        headship on a tie), else whoever the department's head reports to."""
        managers = self.managers_above(dept_id, d, level)
        if managers:
            return min(managers, key=lambda m: (len(self.reports.get(m, ())), m))
        head = self.head(dept_id, d)
        if head and self.employees[head].termination_date != d and self.employees[head].job.job_level >= level:
            return head
        return self.boss_of_head(dept_id, d)

    def open_requisition(self, d: date, *, dept_id: str, level: int, location: str, employment_type: str,
                         position: Position | None = None, hire_date: date | None = None) -> Requisition:
        req_id = f"R{self._next('R')}"
        rng = self.rng("req", req_id)
        rc = self.cfg["recruiting"]
        if position is None:
            position = self.new_position(dept_id, level, d)
        manager = self.hiring_manager(dept_id, level, d)
        # A hire can't be faster than the shortest path through the stages.
        fastest = sum(days[0] for days in rc["stage_days"].values()) + 2
        ttf = max(fastest, int(rng.lognormal(math.log(rc["time_to_fill_days_median"] + 4 * (level - 2)),
                                             rc["time_to_fill_sigma"])))
        if employment_type != "Intern" and rng.random() < rc["cancel_probability"]:
            closed, outcome, start = d + timedelta(days=int(rng.integers(20, 61))), "Cancelled", None
            position.closed_date = closed
        else:
            closed = d + timedelta(days=ttf)
            start = hire_date or next_monday(closed + timedelta(days=int(rng.integers(*rc["start_delay_days"]))))
            if hire_date and closed >= hire_date:
                closed = hire_date - timedelta(days=int(rng.integers(20, 40)))
            outcome = "Filled"
        req = Requisition(req_id, position.position_id, dept_id, manager, level, location, employment_type,
                          d, closed, outcome, start)
        self.requisitions[req_id] = req
        return req

    def senior_share(self, dept_id: str, d: date) -> float:
        """Share of a department's active FTEs at L5 or above."""
        staff = [self.employees[e] for e in self.dept_members[dept_id]
                 if self.employees[e].employment_type == "FTE" and self.active(self.employees[e], d)]
        return sum(e.job.job_level >= 5 for e in staff) / len(staff) if staff else 0.0

    def pipeline(self, d: date, employment_type: str) -> int:
        """Accepted-but-not-started plus still-open requisitions."""
        def pending(r: Requisition) -> bool:
            return r.hire_date > d if r.outcome == "Filled" else r.closed_date > d
        return sum(1 for r in self.requisitions.values()
                   if r.employment_type == employment_type and r.opened_date <= d and pending(r))

    # ------------------------------------------------------------------ events
    def hire_from(self, req: Requisition, d: date) -> None:
        rng = self.rng("hire", req.req_id)
        mv = self.cfg["movement"]
        rehire_of = None
        if req.employment_type == "FTE" and rng.random() < mv["rehire_share_of_hires"]:
            cutoff = add_months(d, -mv["rehire_min_months_away"])
            left_voluntarily = {t.employee_id for t in self.terminations if t.termination_type == "V"}
            latest_hire: dict[int, date] = {}
            for o in self.employees.values():
                latest_hire[o.person_id] = max(latest_hire.get(o.person_id, o.hire_date), o.hire_date)
            former = sorted((e for e in self.employees.values()
                             if e.employment_type == "FTE" and e.termination_date and e.termination_date <= cutoff
                             and e.employee_id in left_voluntarily and e.hire_date == latest_hire[e.person_id]),
                            key=lambda e: e.employee_id)
            if former:
                rehire_of = pick(rng, former)
        emp = self._create_employee(d, req.employment_type, req.location, rng, rehire_of=rehire_of)
        if req.employment_type == "Contractor":
            emp.planned_end = add_months(d, int(rng.integers(*self.cfg["contractors"]["contract_months"])))
        elif req.employment_type == "Intern":
            emp.planned_end = last_weekday(date(d.year, 8, 31))
        pos = self.positions[req.position_id]
        self.start_job(emp, d, "Rehire" if rehire_of else "Hire", position=pos, level=pos.grades[-1][1],
                       location=req.location, salary=self.salary(pos.grades[-1][1], req.location, rng))
        emp.job.manager_id = req.hiring_manager_id
        if not self.manager_valid(emp, d, {req.department_id: self.head(req.department_id, d)}):
            emp.job.manager_id = self.expected_manager(emp, d)
        if emp.job.manager_id:
            self.reports.setdefault(emp.job.manager_id, set()).add(emp.employee_id)
        req.hired_employee_id = emp.employee_id

    def terminate(self, emp: Employee, d: date, ttype: str, reason: str, regretted: bool | None,
                  backfill: bool) -> None:
        emp.termination_date = d
        emp.job.effective_to = d
        self.terminations.append(Termination(f"T{self._next('T'):05d}", emp.employee_id, d, ttype, reason, regretted))
        self.vacate(self.positions[emp.job.position_id], d, backfill, emp.job.location)
        self.dept_members[emp.job.department_id].discard(emp.employee_id)
        self.set_manager(emp, None)

    # ------------------------------------------------------------------ one day
    def simulate_day(self, d: date) -> None:
        """One day of events. The steps share one random stream, so their order is fixed."""
        rng = self.rng("day", d.toordinal())
        self._pending_backfills: list[tuple[Position, int, str]] = []
        self._start_new_hires(d)
        self._process_exits(d, rng)
        staff = [e for e in self.active_ids(d, "FTE")
                 if self.employees[e].termination_date != d and self.employees[e].hire_date < d]
        self._promotions_and_merit(d, staff, rng)
        self._transfers_and_reorgs(d, staff, rng)
        self._off_cycle_changes(d, staff, rng)
        for pos, level, location in self._pending_backfills:
            self.open_requisition(d, dept_id=pos.department_id, level=level, location=location,
                                  employment_type="FTE", position=pos)
        self._open_requisitions(d, rng)
        self._fix_reporting_lines(d)

    def _fix_reporting_lines(self, d: date) -> None:
        """After the day's moves: anyone whose manager left, moved or is no longer senior gets a new one."""
        heads = {dept: self.head(dept, d) for dept in self.departments}
        for emp_id in self.active_ids(d):
            emp = self.employees[emp_id]
            if emp.termination_date == d or self.manager_valid(emp, d, heads):
                continue
            old = self.employees.get(emp.job.manager_id) if emp.job.manager_id else None
            # A manager who leaves today still manages today; everything else applies at once.
            start = d + timedelta(days=1) if old and old.termination_date == d else d
            self.change(emp, start, "Manager Change", manager_id=self.expected_manager(emp, d))

    def _start_new_hires(self, d: date) -> None:
        starting = (r for r in self.requisitions.values() if r.outcome == "Filled" and r.hire_date == d)
        for req in sorted(starting, key=lambda r: r.req_id):
            self.hire_from(req, d)

    def _process_exits(self, d: date, rng: np.random.Generator) -> None:
        att = self.cfg["attrition"]
        for emp_id in self.active_ids(d):
            emp = self.employees[emp_id]
            if emp.hire_date == d or emp_id == self.ceo_id:
                continue
            if emp.planned_end == d:
                self.terminate(emp, d, "I", "END_OF_TERM", None, backfill=False)
                continue
            if emp.employment_type != "FTE":
                continue
            p_v = self.p_vol * self.seasonality[d.month] * att["level_multiplier"][emp.job.job_level]
            if (d - emp.hire_date).days < 365:
                p_v *= att["first_year_multiplier"]
            u = rng.random()
            if u < p_v:
                ttype = "V"
            elif u < p_v + self.p_invol:
                ttype = "I"
            elif (u < p_v + self.p_invol + self.p_retire and emp.job.job_level >= 3
                  and (d - emp.hire_date).days >= att["retirement_min_service_years"] * 365):
                ttype = "R"
            else:
                continue
            reasons = att["reasons"][ttype]
            reason = str(rng.choice(list(reasons), p=normalised(list(reasons.values()))))
            regretted = bool(rng.random() < att["regretted_share_of_voluntary"]) if ttype == "V" else None
            self.terminate(emp, d, ttype, reason, regretted, backfill=rng.random() < att["backfill_probability"])

    def _promotions_and_merit(self, d: date, staff: list[str], rng: np.random.Generator) -> None:
        mv = self.cfg["movement"]
        if d.day == 1 and d.month in mv["promotion_cycle_months"]:
            per_cycle = mv["promotion_annual"] / len(mv["promotion_cycle_months"])
            eligible = [e for e in staff if self.employees[e].job.job_level < 6
                        and add_months(self.employees[e].level_since, mv["min_months_in_level_for_promotion"]) <= d]
            weight = np.array([mv["promotion_level_multiplier"][self.employees[e].job.job_level] for e in eligible],
                              dtype=float)
            p_each = np.minimum(0.9, per_cycle * len(staff) * weight / max(1.0, weight.sum()))
            for emp_id, p in zip(eligible, p_each, strict=True):
                if rng.random() >= p:
                    continue
                emp = self.employees[emp_id]
                level = emp.job.job_level + 1
                salary = max(self.round_pay(emp.job.salary_local * (1 + rng.uniform(*mv["promotion_raise"])),
                                            emp.job.location),
                             self.band_floor(level, emp.job.location))
                self.promote(emp, d, min(salary, self.band_max(level, emp.job.location)))
        if d.day == 1 and d.month == mv["merit_month"]:
            spread = self.cfg["band_spread"]
            for emp_id in staff:
                emp = self.employees[emp_id]
                if emp.job.effective_from == d or (d - emp.hire_date).days < 180:
                    continue
                # Merit matrix: people low in their band get more, people near the top less.
                scale = min(1.5, max(0.25, 1 + (1 - self.compa_ratio(emp)) / (2 * spread)))
                salary = min(self.round_pay(emp.job.salary_local * (1 + scale * rng.uniform(*mv["merit_raise"])),
                                            emp.job.location),
                             self.band_max(emp.job.job_level, emp.job.location))
                if salary > emp.job.salary_local:
                    self.change(emp, d, "Merit Increase", salary_local=salary)

    def _transfers_and_reorgs(self, d: date, staff: list[str], rng: np.random.Generator) -> None:
        """Department transfers, manager changes from reorgs, and in-place name changes."""
        for emp_id in staff:
            emp = self.employees[emp_id]
            if emp.job.effective_from == d or emp_id == self.ceo_id:
                continue
            u = rng.random()
            if u < self.p_transfer and emp.job.job_level < 6:
                self._transfer(emp, d, rng)
            elif u < self.p_transfer + self.p_manager_change:
                self._reorg(emp, d, rng)
            if rng.random() < self.p_name_change:
                fake = self.fakers[self.locations[emp.job.location]["faker_locale"]]
                fake.seed_instance(int(rng.integers(0, 2**31)))
                last = fake.last_name()
                emp.name_changes.append((d, last, self.unique_email(emp.first_name, last)))

    def _reorg(self, emp: Employee, d: date, rng: np.random.Generator) -> bool:
        alternatives = self.managers_above(emp.job.department_id, d, emp.job.job_level,
                                           exclude=(emp.employee_id, emp.job.manager_id))
        if not alternatives or self.head(emp.job.department_id, d) == emp.employee_id:
            return False
        self.change(emp, d, "Manager Change", manager_id=pick(rng, alternatives))
        return True

    def _transfer(self, emp: Employee, d: date, rng: np.random.Generator) -> None:
        choices = [x for x in self.leaf_departments if x != emp.job.department_id]
        new_dept = str(rng.choice(choices, p=normalised([self.departments[x]["weight"] for x in choices])))
        location, salary = emp.job.location, emp.job.salary_local
        if rng.random() < self.cfg["movement"]["transfer_location_change_probability"]:
            location = str(rng.choice([x for x in self.location_names if x != emp.job.location]))
            salary = self.salary(emp.job.job_level, location, rng)
        backfill = rng.random() < self.cfg["attrition"]["backfill_probability"]
        self.vacate(self.positions[emp.job.position_id], d - timedelta(days=1), backfill, emp.job.location)
        new_pos = self.new_position(new_dept, emp.job.job_level, d)
        new_pos.occupied.append(Stint(d))
        self.change(emp, d, "Transfer", position_id=new_pos.position_id, department_id=new_dept,
                    location=location, salary_local=salary, currency=self.locations[location]["currency"],
                    manager_id=None)
        self.change(emp, d, "Transfer", manager_id=self.expected_manager(emp, d))

    def _off_cycle_changes(self, d: date, staff: list[str], rng: np.random.Generator) -> None:
        """Equity adjustments for clearly underpaid people (after six months' tenure, at most once a
        year) and occasional relocations."""
        mv = self.cfg["movement"]
        unchanged = [e for e in staff if self.employees[e].job.effective_from != d and e != self.ceo_id]
        underpaid = [e for e in unchanged
                     if self.compa_ratio(self.employees[e]) < mv["equity_adjustment_below_compa"]
                     and (d - self.employees[e].hire_date).days >= 180
                     and (d - self.last_adjusted.get(e, date.min)).days >= 365]
        for _ in range(int(rng.poisson(mv["off_cycle_changes_per_day"]))):
            if rng.random() < mv["off_cycle_relocation_share"] and unchanged:
                emp = self.employees[unchanged.pop(int(rng.integers(len(unchanged))))]
                location = str(rng.choice([x for x in self.location_names if x != emp.job.location]))
                self.change(emp, d, "Location Change", location=location,
                            currency=self.locations[location]["currency"],
                            salary_local=self.salary(emp.job.job_level, location, rng))
            elif underpaid:
                emp = self.employees[underpaid.pop(int(rng.integers(len(underpaid))))]
                target = self.round_pay(self.band_mid(emp.job.job_level, emp.job.location), emp.job.location)
                salary = min(self.round_pay(emp.job.salary_local * (1 + rng.uniform(*mv["off_cycle_raise"])),
                                            emp.job.location), target)
                if salary > emp.job.salary_local:
                    self.change(emp, d, "Salary Adjustment", salary_local=salary)
                    self.last_adjusted[emp.employee_id] = d

    def _open_requisitions(self, d: date, rng: np.random.Generator) -> None:
        """Growth hiring to the headcount plan, contractor cover, and the summer intern class."""
        cfg = self.cfg
        # Open enough requisitions that, after the exits expected while they are being filled,
        # FTEs land on the plan when the new starters arrive.
        lookahead = cfg["recruiting"]["planning_lookahead_days"]
        active_fte = len(self.active_ids(d, "FTE"))
        expected_exits = active_fte * self.expected_exit_rate * lookahead / 365.25
        projected = active_fte + self.pipeline(d, "FTE") - expected_exits
        need = math.ceil(self.target_fte(d + timedelta(days=lookahead)) - projected)
        for _ in range(max(0, min(need, 4))):
            dept = str(rng.choice(self.leaf_departments, p=self.dept_weights))
            level = int(rng.choice(self.level_ids[:6], p=normalised(self.level_shares[:6])))
            if level >= 5 and self.senior_share(dept, d) >= cfg["recruiting"]["max_senior_share"]:
                level = int(rng.choice(self.level_ids[:4], p=normalised(self.level_shares[:4])))
            self.open_requisition(d, dept_id=dept, level=level, employment_type="FTE",
                                  location=str(rng.choice(self.location_names, p=self.location_shares)))
        target_contractors = cfg["company"]["contractor_share_of_fte"] * len(self.active_ids(d, "FTE"))
        if len(self.active_ids(d, "Contractor")) + self.pipeline(d, "Contractor") < target_contractors - 1:
            self.open_requisition(d, dept_id=str(rng.choice(self.leaf_departments, p=self.dept_weights)),
                                  level=int(rng.choice(cfg["contractors"]["levels"])), employment_type="Contractor",
                                  location=str(rng.choice(self.location_names, p=self.location_shares)))
        if d.month == 2 and d.day == 1:
            interns = cfg["interns"]
            for _ in range(interns["per_summer"]):
                dept = str(rng.choice(interns["departments"]))
                self.open_requisition(d, dept_id=dept, level=1, employment_type="Intern",
                                      location=str(rng.choice(interns["locations"])),
                                      hire_date=first_weekday(date(d.year, 6, 1)))

    def run(self, as_of: date) -> Workforce:
        self.build_opening_population()
        d = self.start
        while d <= as_of:
            self.simulate_day(d)
            d += timedelta(days=1)
        self.simulated_to = as_of
        return self

    # ------------------------------------------------------------------ what the HRIS shows on a date
    def event_time(self, key: object, d: date) -> datetime:
        rng = self.rng("ts", key, d.toordinal())
        return datetime.combine(d, time(int(rng.integers(8, 19)), int(rng.integers(0, 60)), int(rng.integers(0, 60))))

    def materialize(self, as_of: date) -> dict[str, list[dict]]:
        if self.simulated_to is None or as_of > self.simulated_to:
            raise ValueError(f"can't show {as_of}: simulated only to {self.simulated_to}")
        return {"hris_employees": self._employees_as_of(as_of),
                "hris_job_history": self._job_history_as_of(as_of),
                "hris_positions": self._positions_as_of(as_of),
                "hris_departments": [{"department_id": d["id"], "department_name": d["name"],
                                      "parent_department_id": d["parent"], "cost_center": d["cost_center"]}
                                     for d in self.cfg["departments"]],
                "hris_terminations": [{"termination_id": t.termination_id, "employee_id": t.employee_id,
                                       "termination_date": t.termination_date, "termination_type": t.termination_type,
                                       "reason_code": t.reason_code, "regretted_flag": t.regretted_flag}
                                      for t in self.terminations if t.termination_date <= as_of],
                "ats_requisitions": self._requisitions_as_of(as_of)}

    def _employees_as_of(self, as_of: date) -> list[dict]:
        rows = []
        for emp in sorted(self.employees.values(), key=lambda e: e.employee_id):
            if emp.hire_date > as_of:
                continue
            last, email = emp.last_name, emp.email
            changed = [emp.hire_date]
            for d, new_last, new_email in emp.name_changes:
                if d <= as_of:
                    last, email = new_last, new_email
                    changed.append(d)
            terminated = emp.termination_date is not None and emp.termination_date <= as_of
            if terminated:
                changed.append(emp.termination_date)
            rows.append({
                "employee_id": emp.employee_id, "first_name": emp.first_name, "last_name": last, "email": email,
                "hire_date": emp.hire_date, "termination_date": emp.termination_date if terminated else None,
                "status": "Terminated" if terminated else "Active", "employment_type": emp.employment_type,
                "updated_at": self.event_time(emp.employee_id, max(changed)),
            })
        return rows

    def _job_history_as_of(self, as_of: date) -> list[dict]:
        rows = []
        for job in self.jobs:
            if job.effective_from > as_of:
                continue
            end = job.effective_to
            shown_end = OPEN_END
            if end is not None:
                emp = self.employees[job.employee_id]
                ended_by_exit = emp.termination_date == end and emp.job is job
                known_on = end if ended_by_exit else end + timedelta(days=1)
                if known_on <= as_of:
                    shown_end = end
            rows.append({
                "job_history_id": job.job_history_id, "employee_id": job.employee_id,
                "effective_from": job.effective_from, "effective_to": shown_end, "position_id": job.position_id,
                "department_id": job.department_id, "manager_id": job.manager_id, "location": job.location,
                "job_level": f"L{job.job_level}", "salary_local": job.salary_local, "currency": job.currency,
                "change_reason": job.change_reason,
            })
        return rows

    def _positions_as_of(self, as_of: date) -> list[dict]:
        rows = []
        for pos in sorted(self.positions.values(), key=lambda p: p.position_id):
            if pos.created_at > as_of:
                continue
            _, level, title = next(g for g in reversed(pos.grades) if g[0] <= as_of)
            filled = any(s.start <= as_of and (s.end is None or s.end >= as_of) for s in pos.occupied)
            closed = pos.closed_date if pos.closed_date and pos.closed_date <= as_of else None
            rows.append({"position_id": pos.position_id, "job_title": title, "department_id": pos.department_id,
                         "job_level": f"L{level}", "is_filled": filled,
                         "created_at": self.event_time(pos.position_id, pos.created_at), "closed_date": closed})
        return rows

    def _requisitions_as_of(self, as_of: date) -> list[dict]:
        rows = []
        for r in sorted(self.requisitions.values(), key=lambda r: r.req_id):
            if r.opened_date > as_of:
                continue
            closed = r.closed_date <= as_of
            rows.append({"req_id": r.req_id, "position_id": r.position_id, "department_id": r.department_id,
                         "hiring_manager_id": r.hiring_manager_id, "opened_date": r.opened_date,
                         "closed_date": r.closed_date if closed else None,
                         "status": r.outcome if closed else "Open"})
        return rows
