# Reconciliation: five P1 metrics, against the source and against Visier's definitions

Two questions about the warehouse's most-used people metrics. Are they right? And how far are they
from what the same company would see in Visier, and why? This is part of the rehearsed migration in
[visier-migration-plan.md](visier-migration-plan.md). The metrics are the P1 metric rows of the
[migration inventory](migration-inventory.csv) (Turnover Count is reconciled as Turnover Rate's
numerator), and [scripts/reconcile.py](../scripts/reconcile.py) produces every number below
(`make migration-check`).

**This is a rehearsal.** Nodewell Labs is fictional and its data is synthetic. There is no Visier
tenant: the warehouse plays the migration target, and every "Visier-style" figure is Visier's public
definition applied to the source data by the script. None was exported from Visier. Where Visier's
docs leave a detail open, the choice made here is written down as an assumption and its effect is
measured. The matching question for the client is in the migration plan's open questions.

**Run on 2026-10-15**, on source data up to 2026-10-14 and the warehouse built up to 2026-10-14.
**Result:** Level 1, 30 of 30 PASS. Level 2, 6 PASS, 6 EXPLAINED, 0 FAIL.

## What was reconciled, and why two levels

| Metric | Warehouse name ([metric-definitions.md](metric-definitions.md)) | Visier name |
| --- | --- | --- |
| Headcount | Headcount | Headcount (`employeeCount`) |
| Average headcount | Average headcount | Average Headcount |
| Turnover rate | Attrition rate | Turnover Rate |
| Resignation rate | Voluntary attrition rate | Resignation Rate |
| Time to hire | Time to hire | Time to Hire |

**Closed periods only:** Q3 2026 (2026-07-01 to 2026-09-30), calendar 2025, and headcount at each of
the 22 month-ends from 2024-12-31 to 2026-09-30. The source moves on one business day per nightly run,
and BigQuery is loaded at the start of that run, so the warehouse can be a day behind the source.
Comparing "today" would mean comparing two different days. The script won't reconcile a period until
the warehouse's reporting date has passed its end.

**Two levels, because they answer different questions.**

- **Level 1, same definitions: is the pipeline right?** Each metric is recomputed exactly as
  [metric-definitions.md](metric-definitions.md) defines it, but independently: from the source
  Postgres (schema `hris`, read-only), in plain SQL and Python, with no dbt and no BigQuery. The result
  is compared with the same metric from the BigQuery marts. Any difference is a pipeline or sync
  defect. Tolerance: counts exact, rates 0.05 percentage points (pp), average headcount 0.05, time to
  hire 0.05 days.
- **Level 2, Visier's definitions: what will users see change, and why?** The same metrics, as Visier
  documents them, are emulated from the source and compared with the warehouse metric. Differences are
  expected. Each has to be traced to a named definition difference, and its size measured.

Comparing only against Visier would hide defects. On 2026-09-30 the Visier-style headcount is 21 above
the warehouse's, and the 21 contractors employed that day account for it. Had the pipeline lost two
full-time records, the gap would be 23, and "it's the contractors" would still sound like an answer.
Level 1 shows first that the warehouse matches its source, so Level 2 has only definitions left to
explain.

## Level 1: same definitions

**How the source side is computed**

- **Headcount and average headcount** come from `hris_employees` alone. A record counts as employed from
  `hire_date` to `termination_date` inclusive (a leaver still counts on their last day). The warehouse
  takes a different route: job-history versions in `dim_employee`, expanded by `fct_headcount_daily`.
  Average headcount adds up the records employed on each day of the period and divides by the number of
  days.
- **Exits** come from `hris_terminations` joined to `hris_employees`, full-time records only, counted by
  `termination_date`. Voluntary means type `V`.
- **Time to hire** uses applications to full-time requisitions whose offer was accepted in the period,
  keeping only a candidate's first application to each requisition, as staging does. The acceptance
  date is the first `hired` stage converted to a UTC date, which is what BigQuery's `date()` does to a
  timestamp.

**Headcount (full-time employees) at each month-end.** Source and warehouse agree at all 22. Each value
below is both:

| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2024 | | | | | | | | | | | | 463 |
| 2025 | 463 | 470 | 475 | 474 | 486 | 488 | 495 | 507 | 513 | 525 | 535 | 540 |
| 2026 | 548 | 550 | 561 | 556 | 560 | 574 | 582 | 589 | 595 | | | |

**Period metrics**

| Metric | Period | Source | Warehouse | Difference | Status |
| --- | --- | --- | --- | --- | --- |
| Average headcount | Q3 2026 | 584.37 | 584.37 | 0.00 | PASS |
| Attrition rate | Q3 2026 | 4.11% (24 exits) | 4.11% (24 exits) | 0.00 pp | PASS |
| Voluntary attrition rate | Q3 2026 | 3.42% (20 exits) | 3.42% (20 exits) | 0.00 pp | PASS |
| Time to hire (mean) | Q3 2026 | 26.23 days (40 offers) | 26.23 days (40 offers) | 0.00 days | PASS |
| Average headcount | 2025 | 494.59 | 494.59 | 0.00 | PASS |
| Attrition rate | 2025 | 15.57% (77 exits) | 15.57% (77 exits) | 0.00 pp | PASS |
| Voluntary attrition rate | 2025 | 9.91% (49 exits) | 9.91% (49 exits) | 0.00 pp | PASS |
| Time to hire (mean) | 2025 | 26.39 days (158 offers) | 26.39 days (158 offers) | 0.00 days | PASS |

**What it shows.** The marts reproduce the source exactly: the same headcount at every month-end, and
the same person-days, exits and accepted offers in each period. The two sides take different routes (the
warehouse counts through job-history versions, the source check through employment dates alone), so
the match also shows that, in the reconciled periods, the SCD2 versions in `dim_employee` add up to the
same number of person-days as the employment dates. Rehires were a suspected edge case, but they cause
no difference. Both sides count employment records (a returning person gets a new `employee_id`), and in
this data no person holds two records on the same day.

## Level 2: Visier's definitions

### The definitions, and how each is emulated

| Visier metric | As Visier documents it | Emulated from the source as |
| --- | --- | --- |
| Headcount (`employeeCount`) | `on Employee filterBy(isActiveEmployee) aggregate count(EmployeeID)` ([metric examples](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-examples.htm)), taken at "the last instant in the period": for January 2019, "January 31, 2019 at 23:59:59.999" ([time handling](https://docs.visier.com/visier-people/Analytic%20Model/metrics/metrics-time.htm)). "By default, Visier's Headcount metric includes all current employees" ([use case](https://docs.visier.com/visier-people/Platform/use-cases/build.htm)) | Employment records of every type employed on the period's last day, including anyone whose last day it is |
| Average Headcount | `forInstantsInInterval(interval, month)` with `averageOverTime(count(EmployeeID))`, "approximating the integral using the trapezoid rule". February 2023 is (962 + 965) / 2, the January and February month-ends ([metric examples](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-examples.htm)) | Headcount of every type at the month-end before the period and at each month-end in it, weighted (h0/2 + h1 + ... + hn/2) / n. The weights for more than one month are inferred (see assumptions) |
| Turnover Rate | "Turnover Rate is calculated as the Turnover Count divided by Average Headcount" ([aggregate thresholds](https://docs.visier.com/developer/Studio/solution%20settings/aggregate-thresholds-set.htm)). Someone who "worked April 30, then exited at the end of the day ... is counted in May exits, not April" ([events](https://docs.visier.com/visier-people/Analytic%20Model/analytic-objects/events-create.htm)) | Exits of every type divided by the Average Headcount above. An exit counts in the period that contains the day after the last day employed |
| Resignation Rate | Resignation, Retirement and Other Voluntary Turnover counts "sum up to the Voluntary Turnover Count" ([concepts](https://docs.visier.com/developer/Analytic%20Model/concepts/concepts.htm)), and "Resignations do not include retirements" ([resignation report](https://www.visier.com/research/resignation-report/)) | Exits of type `V` (every `VOL_*` reason) divided by the same Average Headcount |
| Voluntary Turnover Rate (a naming check, not a P1 metric) | Resignations plus retirements plus other voluntary exits, as above | Types `V` and `R` divided by the same Average Headcount |
| Time to Hire | "from the moment they apply until they accept the offer" ([HR metrics](https://www.visier.com/hr-metrics/)). "Hire Date, Hire Type, and Time to Hire are properties of the Employee Start event" ([objects](https://docs.visier.com/developer/Studio/getting%20started/objects-visier-cloud.htm)) | Mean days from applying to accepting the offer, for employment records of every type that started in the period |

Rates on both sides are for the period, not annualised. Visier can produce annualised and
trailing-12-month versions of a metric: Annualized and Annualized Rate are derived metric types
([metrics](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-create.htm)), and its formula
language has `annualize()` (example `annualize(EmployeeExitRate)`) and `trailing12Months()` (example
`trailing12Months(EmployeeExitCount)`) ([VFL](https://docs.visier.com/visier-people/Analytic%20Model/metrics/visier-formula-language.htm)).
Which version the tenant reports is open question 4, so the period rates are like for like only against
a period-rate figure. Annualisation isn't reconciled here.

### Tolerances, and what PASS, EXPLAINED and FAIL mean

- **Tolerances**, as proposed in section 7 of the migration plan (to be agreed with the client in
  discovery): headcount exact, rates ±0.5 pp, averages (average headcount and time to hire) within 2% of
  the warehouse value.
- **Restatement.** For each row, the warehouse value is restated on the warehouse's own marts, one
  documented definition difference at a time, and each step's size is recorded. What is left after the
  last step (the Visier-style value minus the fully restated warehouse value) is the **residual**. By
  then both sides use the same definition, so the residual is a data difference. A lost record shows up
  in the residual; it can't hide inside a definition step.
- **PASS:** the difference is within tolerance and the residual is within the Level 1 tolerance.
  **EXPLAINED:** the difference is outside tolerance and the residual is within the Level 1 tolerance, so
  documented definition differences account for all of it. **FAIL:** a residual outside the Level 1
  tolerance, whatever the size of the difference. Level 1 covers full-time staff only, so for
  contractors and interns the residual is the only check.
- **Step order.** The steps always run in the same order: method, retirements, exit period, then
  population. Each step's size depends on the ones before it; the total doesn't. Population comes last
  because it is the largest step and the one a client is most likely to decide differently.
- **Contractor and intern exits** aren't in any mart, because `fct_terminations` is full-time only by
  design. The restatement reads them from `stg_hris__terminations`.
- **Tested.** With records removed in memory from the warehouse side (two contractors on 2026-09-30, one
  full-time employee on 2026-08-31, and one full-time and one contractor exit in Q3 2026), Level 1 failed
  on that headcount and that attrition rate. Level 2 showed residuals of +2 on headcount, +0.67 on
  average headcount and +0.32 pp on the turnover rate, and all three rows failed. Removing the four
  contractor starts of Q3 2026 left Time to Hire within tolerance but with a +0.43 day residual, and the
  row failed; Level 1 passed, because it doesn't cover contractors.

### Results

| Visier metric | Period | Warehouse | Visier-style | Difference | Tolerance | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Headcount | 2026-09-30 | 595 | 616 | +21 | exact | EXPLAINED |
| Average Headcount | Q3 2026 | 584.37 | 617.00 | +32.63 | ±11.69 | EXPLAINED |
| Turnover Rate | Q3 2026 | 4.11% (24 exits) | 6.65% (41 exits) | +2.54 pp | ±0.5 pp | EXPLAINED |
| Resignation Rate | Q3 2026 | 3.42% (20 exits) | 3.24% (20 exits) | -0.18 pp | ±0.5 pp | PASS |
| Voluntary Turnover Rate | Q3 2026 | 3.42% (20 exits) | 3.40% (21 exits) | -0.02 pp | ±0.5 pp | PASS |
| Time to Hire | Q3 2026 | 26.23 days (40 offers) | 25.92 days (49 starts) | -0.31 days | ±0.52 days | PASS |
| Headcount | 2025-12-31 | 540 | 562 | +22 | exact | EXPLAINED |
| Average Headcount | 2025 | 494.59 | 515.33 | +20.74 | ±9.89 | EXPLAINED |
| Turnover Rate | 2025 | 15.57% (77 exits) | 20.76% (107 exits) | +5.19 pp | ±0.5 pp | EXPLAINED |
| Resignation Rate | 2025 | 9.91% (49 exits) | 9.51% (49 exits) | -0.40 pp | ±0.5 pp | PASS |
| Voluntary Turnover Rate | 2025 | 9.91% (49 exits) | 9.51% (49 exits) | -0.40 pp | ±0.5 pp | PASS |
| Time to Hire | 2025 | 26.39 days (158 offers) | 26.21 days (187 starts) | -0.18 days | ±0.53 days | PASS |

The warehouse column is the warehouse's own metric: Headcount, Average headcount and Attrition rate as
named, and the voluntary attrition rate for both Resignation Rate and Voluntary Turnover Rate.

### Where each difference comes from

Every residual is 0, so in this run each difference is entirely a definition difference.

**Headcount: who is counted.** +21 on 2026-09-30 and +22 on 2025-12-31 are the contractors employed on
those days. No interns were employed on either date: the summer interns left on 2026-08-31 and
2025-08-29. Timing adds nothing, because both sides take the last day of the period and keep a leaver in
that day's count. In Visier's terms the difference is the Headcount population: the `isActiveEmployee`
concept plus any filter the tenant added to the Headcount formula (Visier's example of excluding
contingent workers edits the formula). In the warehouse it is `is_fte`. Both sides count people, one per
employment record. "FTE" in this warehouse is an employment type, not a sum of FTE fractions.

**Average Headcount: how it's sampled, and who is counted.**

| Step | Q3 2026 | 2025 |
| --- | --- | --- |
| Warehouse: mean of the daily full-time headcount | 584.37 | 494.59 |
| Month-end trapezoid instead of the daily mean (full-time) | +0.80 → 585.17 | -0.22 → 494.38 |
| Contractors and interns counted | +31.83 → 617.00 | +20.96 → 515.33 |
| Residual | 0.00 | 0.00 |

For Q3 2026 the Visier-style value is (608/2 + 617 + 622 + 616/2) / 3 = 617.00, using every employment
type on 2026-06-30, 07-31, 08-31 and 09-30. The method step is small for full-time staff, whose numbers
move smoothly. It is large for people who come and go within a month, because month-end sampling only
sees who is there on the last day. The 2026 interns (2026-06-01 to 2026-08-31) were there on three of
the four month-ends, so they count as 8.33 people in Q3's average, though they averaged 6.74 over its 92
days. The 2025 interns left on 2025-08-29, two days before a month-end, so they count as 1.67 people over
2025, against 2.44 measured day by day.

**Turnover Rate: contract and internship ends.**

| Step | Q3 2026 | 2025 |
| --- | --- | --- |
| Warehouse attrition rate | 4.11% (24 / 584.37) | 15.57% (77 / 494.59) |
| Denominator by the month-end trapezoid (full-time) | -0.01 pp | +0.01 pp |
| Exit counted in the period of the day after the last day employed | 0.00 pp: no exit dated 2026-06-30 or 2026-09-30 | 0.00 pp: no exit dated 2024-12-31 or 2025-12-31 |
| Contractors and interns counted | +2.54 pp: +17 exits (7 contracts and 10 internships ending); denominator 585.17 → 617.00 | +5.19 pp: +30 exits (20 contracts, 10 internships); denominator 494.38 → 515.33 |
| Residual | 0.00 pp | 0.00 pp |
| Visier-style | 6.65% (41 / 617.00) | 20.76% (107 / 515.33) |

This is the difference users would notice most: 2025 turnover would read 20.76% instead of 15.57%. The
HRIS records contract and internship ends as exits (type `I`, reason `END_OF_TERM`). So if contingent
workers are loaded into Visier as employees, their endings become Employee Exits like any other. The
warehouse leaves them out on purpose: a planned ending isn't attrition.

**Resignation Rate: the denominator only.** The numerator is the same on both sides (20 in Q3 2026, 49
in 2025). Contractors and interns never resign in this data: all of their exits are `END_OF_TERM`. The
whole difference is the denominator, -0.18 pp and -0.40 pp, and almost all of that comes from
contractors and interns in the average headcount. 2025 is close to the edge (-0.40 against a ±0.5 pp
tolerance). A workforce with more contingent workers would breach it.

**Voluntary Turnover Rate: a pass by coincidence.** In Q3 2026 the one retirement adds +0.17 pp (21
exits instead of 20), and the larger denominator takes off 0.19 pp, so the difference nets to -0.02 pp.
2025 had no retirements. Visier's Voluntary Turnover Rate and the warehouse's voluntary attrition rate
measure different things even when the numbers agree. The like-for-like pair is Resignation Rate.

**Time to Hire: which hires are counted.**

| Step | Q3 2026 | 2025 |
| --- | --- | --- |
| Warehouse: full-time offers accepted in the period | 26.23 days (40 offers) | 26.39 days (158 offers) |
| Full-time employees who started in the period instead | -0.74 → 25.49 (45 starts) | +0.02 → 26.40 (154 starts) |
| Contractors and interns counted | +0.43 → 25.92 (4 contractors) | -0.19 → 26.21 (23 contractors, 10 interns) |
| Residual | 0.00 | 0.00 |

Both sides measure the same span, from applying to accepting. What changes is who is counted. Only 26
of the 45 full-time employees who started in Q3 2026 also accepted their offer in Q3; the other 19
accepted before July.

### Assumptions and their effect

| Assumption | Why it is an assumption | Alternative | Effect on these periods |
| --- | --- | --- | --- |
| Trapezoid weights for several months: (h0/2 + h1 + ... + hn/2) / n | Visier's worked example covers a single month, where the rule reduces to (opening + closing) / 2. The weights for a quarter or a year are inferred from "the trapezoid rule" (open question 4) | Equal weights on the n + 1 month-ends | Q3 2026: 617.00 against 615.75 (Turnover Rate 6.65% against 6.66%). 2025: 515.33 against 515.85 (20.76% against 20.74%). No status changes |
| An exit counts in the period that contains the day after the last day employed | Visier's events page states the rule for "metrics that track movement", and its system-events page says terminations on a month's last day "may count toward the next month's turnover" ([system events](https://docs.visier.com/developer/Analytic%20Model/analytic-objects/system-events.htm)). The "may", and the page listing it among scenarios that trigger system termination events, leave the tenant's setup to confirm (open question 3) | Count exits by the last day employed | None for these periods: no exit falls on a period's last day. It matters by month: from December 2024 to September 2026, 15 exits fall on a month's last day (4 full-time, 1 contractor, and the 10 interns on 2026-08-31). The rule used here counts each of them in the following month |
| `termination_date` is the last day worked, and the person is in that day's headcount | It matches the April 30 example if the tenant maps the termination date that way (open question 3). Visier's system-events page says the system "treats the last working day as the termination date" and may move a first-non-working-day date back one day | The termination date is the first day not employed | The 10 interns drop out of the 2026-08-31 headcount: Q3 2026 Average Headcount 613.67, Turnover Rate 6.68%. 2025: 515.08, 20.77%. No status changes |
| Every employment type is in the population | Visier's Headcount includes all current employees by default, but a tenant can change it to exclude contingent employees ([use case](https://docs.visier.com/visier-people/Platform/use-cases/build.htm)) (open question 1) | Full-time only, as for a tenant that excludes contingent workers | Q3 2026: Headcount 595, Average Headcount 585.17, Turnover Rate 4.10%, Resignation Rate 3.42%, Time to Hire 25.49 days. 2025: 540, 494.38, 15.58%, 9.91%, 26.40 days. Every row would PASS except Time to Hire in Q3 2026 (-0.74 days against ±0.52), which the start-date step explains |
| Resignation Rate's denominator is Average Headcount | Visier documents the denominator for Turnover Rate only (open question 4) | Another denominator, as set in the metric's Settings | Not measurable without the tenant's metric Settings |
| Every `VOL_*` reason is a Resignation, and `END_OF_TERM` is an involuntary exit | Each tenant maps reasons to its exit categories (open question 5) | Some `VOL_*` reasons filed as Other Voluntary | Resignation Rate would fall by those exits; Voluntary Turnover Rate would not change |
| Time to Hire is a mean, starting from the application date | Mean or median isn't public. Another Visier post starts the clock at "applying or being sourced" ([strategic HR metrics](https://www.visier.com/blog/top-strategic-hr-metrics/)), and the ATS has no sourcing date (open question 8) | Median | Medians: 25.0 days (Q3 2026) and 26.0 days (2025) |

**Not emulated.** Some variances only exist on the Visier side, so an emulation from the source can't
reproduce them:

- System Start and System Termination events, which Visier generates "to fill in gaps"
  ([system events](https://docs.visier.com/developer/Analytic%20Model/analytic-objects/system-events.htm)).
- Exits Visier infers when a member disappears from a temporal load, and rolling restatements
  ([data loads](https://docs.visier.com/developer/Studio/data/data-loads.htm)).
- Aggregate thresholds that hide small populations; "No threshold is applied by default"
  ([aggregate thresholds](https://docs.visier.com/developer/Studio/solution%20settings/aggregate-thresholds-set.htm)).

Once a real Visier export is in the comparison, each of these becomes a variance class of its own.

## Decisions for the client

Four of the decisions below are written up in section 4 of the [migration plan](visier-migration-plan.md)
(4.1, 4.2, 4.3 and 4.6), and these recommendations follow it. Whether contract and internship ends count
as turnover follows from 4.1, and Time to Hire by start date comes out of this reconciliation. Once a
decision is made, it goes into [decisions.md](decisions.md) and
[metric-definitions.md](metric-definitions.md).

| Decision | What it moves (Level 2 above) | Options | Recommendation, and how both definitions stay available |
| --- | --- | --- | --- |
| Who Headcount counts | +21 at 2026-09-30 (+3.5%), +22 at 2025-12-31 | Adopt Visier's population; keep full-time only; both | Both. A population flag in `dim_employee` reproduces the tenant's Headcount population (`isActiveEmployee` plus any filter added to the Headcount formula), and "Headcount" follows it. The current measure becomes "Headcount (full-time employees)". Avoid "FTE headcount": in Visier, FTE is a separate factor ([Spring 2019 release notes](https://docs.visier.com/visier-people/Release%20Notes/2019/SP19-release-notes.htm)), and a sum of FTE fractions is a different number |
| How Average Headcount is sampled | +0.80 (Q3 2026) and -0.22 (2025) for full-time staff; much more for short stays such as internships | Daily mean; month-end trapezoid; both | Keep the daily mean as "Average Headcount", because it follows waves within a month. Add "Average Headcount (Visier method)" from month-end snapshots, for the parallel run and for comparisons with old Visier reports |
| Whether contract and internship ends count as turnover | +2.54 pp (Q3 2026), +5.19 pp (2025) | Count them (Visier's default population, if contingent workers are loaded as employees); keep them out | First ask whether the tenant changed Headcount or Turnover to exclude contingent workers (open question 1). Either way, keep "Turnover Rate (full-time employees)" as the current definition. Add "Turnover Rate (all employees)" if the tenant counts them; that also needs contractor and intern exits in a mart, since today they stop at staging |
| Which period an exit falls in | Nothing in these periods; whole exits move between months (August to September 2026 for the 10 interns) | By the last day employed; by the day after; carry both | Carry both dates (proposed `exit_effective_date`). Follow the tenant once an export shows which rule it uses |
| What "voluntary" means | Resignation Rate matches; Voluntary Turnover Rate adds retirements (+0.17 pp in Q3 2026) | Rename; map reasons to Visier's exit categories | Label the warehouse's voluntary rate "Resignation Rate" and add "Voluntary Turnover Rate" (voluntary plus retirement), so neither is compared with the other by mistake |
| Which hires Time to Hire covers | -0.74 days for full-time staff in Q3 2026; +0.02 in 2025 | By offer accepted; by start date | Keep both. "Time to Hire" stays by offer accepted for recruiting, and "Time to Hire (by start date)", from `fct_hires`, is for comparisons with Visier |

## How to re-run

```
make check-inventory                 # offline, no credentials: inventory references and coverage
make migration-check                 # the inventory check, then this reconciliation
```

To run only the reconciliation, with the same environment `make` uses:

```
VIRTUAL_ENV=$PWD/.venv uv run --no-project --env-file .env -- python scripts/reconcile.py
VIRTUAL_ENV=$PWD/.venv uv run --no-project --env-file .env -- python scripts/reconcile.py --level 1   # pipeline only
```

- **What it needs:** `.env` with `POSTGRES_URL` and the BigQuery key. It only reads: the Postgres
  session is set to read-only, and the BigQuery queries are SELECTs, each capped at 1 GiB billed. A run
  takes about 10 seconds and bills about 270 MiB, mostly BigQuery's 10 MiB minimum for each table a
  query reads. That is small next to the 1 TiB of query processing a month in
  [platform-notes.md](platform-notes.md).
- **When:** after a nightly build. The periods are fixed in the script (`PERIODS` and
  `HEADCOUNT_INSTANTS`), so a closed period should give the same answer every time. If it doesn't,
  something has changed history in the source, such as a back-dated correction, and that is worth
  knowing. To reconcile a new close, add it there once the warehouse's reporting date has passed the
  period's end.
- **If it fails** (exit 1):
  - A **Level 1** FAIL is a pipeline or sync defect. Start with `make check-ingestion` (are source,
    landing and BigQuery in step?) and `make freshness`, then compare the failing period's records.
  - A **Level 2** FAIL has a residual: under Visier's definition, the warehouse and the source disagree.
    Usually that is about contractors or interns, whom Level 1 doesn't cover. The other possibility is a
    definition difference that isn't documented yet, which then needs its own step in the script and in
    this page.

**With a real tenant**, the emulated Visier-style values are replaced by the tenant's own figures: an
Aggregated Data Export of metric values
([data exports](https://docs.visier.com/developer/Analytics/data%20export/data-export.htm)) for the
same periods, pulled by a user who can see all data and tied to one data version. Level 1 and the
restatement steps stay as they are. The residual then also includes the Visier-only variances above,
and the exporting user's security: "A user's security access will be enforced when they consume a Data
Export".
