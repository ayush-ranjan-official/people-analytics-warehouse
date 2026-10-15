# Metric definitions

How each people metric is calculated from the `analytics` marts, with the query and a worked example.
Every example number was produced by the query shown, on the synthetic data as of 2026-10-10.

## Rules that apply to every metric

- **FTEs only.** Headcount, attrition and hiring metrics count full-time employees: `is_fte` in
  `fct_headcount_daily`, `employment_type = 'fte'` in `dim_employee`, `fct_requisitions` and
  `fct_application_funnel`. `fct_hires` and `fct_terminations` are already FTE-only. Contractors and
  interns are excluded because a contract or internship ending is planned, not attrition.
- **"Today" is the reporting date**, not the calendar date. The data covers up to the HR system's
  latest business day: `select reporting_date from analytics.int_reporting_date`, the same as
  `max(snapshot_date)` in `fct_headcount_daily`. End any period at that date at the latest.
- **Events, not people.** A rehired person has two `employee_id`s with one `person_id`. Rates count
  events (exits, hires). To count people, use `count(distinct person_id)`.
- **Whole departments** are grouped with `dim_org.division_name`. Engineering's teams sit under a
  parent row that holds only the head of Engineering.
- **Fiscal year** runs April to March and is named after the year it ends: FY2027 is 2026-04-01 to
  2027-03-31 (`dim_date.fiscal_year`, `fiscal_quarter`).
- **Salaries** are in local currency (`currency`). Never sum or average `salary_local` across
  currencies.

## Headcount (as of a date)

FTEs employed on the day. A leaver still counts on their last day.

```sql
select countif(is_fte) as headcount
from analytics.fct_headcount_daily
where snapshot_date = '2026-09-30'
```

**Example:** 2026-09-30 → **595** (616 including contractors and interns).

## Average headcount (period)

The mean of the daily FTE headcount over every day of the period. It's the denominator of every rate
below. Don't use the headcount at the start or end of the period: hiring or exits that come in waves
distort both.

```sql
select count(*) / count(distinct snapshot_date) as average_headcount
from analytics.fct_headcount_daily
where is_fte and snapshot_date between '2026-07-01' and '2026-09-30'
```

**Example:** Q3 2026 (92 days) → **584.4**.

## Attrition rate

Exits in the period ÷ average headcount in the period.

```sql
with exits as (
    select count(*) as exits
    from analytics.fct_terminations
    where termination_date between '2026-07-01' and '2026-09-30'
),
headcount as (
    select count(*) / count(distinct snapshot_date) as average_headcount
    from analytics.fct_headcount_daily
    where is_fte and snapshot_date between '2026-07-01' and '2026-09-30'
)
select exits, average_headcount, round(100 * exits / average_headcount, 2) as attrition_rate_pct
from exits, headcount
```

**Example:** Q3 2026 → 24 ÷ 584.4 = **4.11%**. Annualised (× 365 ÷ 92) that is about 16.3%.
For Engineering, filter both parts on `division_name = 'Engineering'` (join `dim_org` on
`department_id`): 13 ÷ 197.3 = **6.59%**.

## Voluntary attrition rate

As attrition, counting only `termination_type = 'voluntary'` (the person chose to leave).
Involuntary exits and retirements are excluded from the numerator, not the denominator.

**Example:** Q3 2026 → 20 ÷ 584.4 = **3.42%**.

## Regretted attrition rate

As attrition, counting only exits with `regretted_flag = true`: voluntary leavers the manager wanted
to keep. `regretted_flag` is null for involuntary exits and retirements.

**Example:** Q3 2026 → 10 ÷ 584.4 = **1.71%**. Half of the 20 voluntary leavers were regretted.

## Average tenure

For people employed on a date: the mean of (date − `hire_date`), in years. Tenure runs from the
current employment record's `hire_date`, so a rehire's tenure restarts.

```sql
select avg(date_diff(h.snapshot_date, e.hire_date, day)) / 365.25 as average_tenure_years
from analytics.fct_headcount_daily as h
join analytics.dim_employee as e using (employee_key)
where h.is_fte and h.snapshot_date = '2026-09-30'
```

**Example:** 2026-09-30 → **2.76 years** (median 2.21). Tenure *at exit* uses
`fct_terminations.tenure_days_at_exit / 365.25`: the Q3 2026 leavers averaged 2.03 years.

## Time to fill

Days from a requisition opening to its offer being accepted, for FTE requisitions filled in the
period. Report the median next to the mean, because a few hard-to-fill roles pull the mean up.

```sql
select avg(days_to_fill) as average_days,
       approx_quantiles(days_to_fill, 2)[offset(1)] as median_days
from analytics.fct_requisitions
where employment_type = 'fte' and requisition_status = 'filled'
  and closed_date between '2026-07-01' and '2026-09-30'
```

**Example:** Q3 2026, 40 requisitions → mean **52.6 days**, median 47.

## Time to hire

Days from the hired candidate applying to accepting the offer, for FTE offers accepted in the period.
It measures the candidate's experience; time to fill measures the requisition's.

```sql
select avg(date_diff(date(hired_at), applied_date, day)) as average_days
from analytics.fct_application_funnel
where employment_type = 'fte' and reached_hired
  and date(hired_at) between '2026-07-01' and '2026-09-30'
```

**Example:** Q3 2026, 40 accepted offers → mean **26.2 days**, median 25.

Hires counted by **start date** come from `fct_hires.hire_date` instead: 45 FTEs started in Q3 2026.
That differs from the 40 offers accepted in Q3 because people start a few weeks after accepting.

## Offer acceptance rate

Offers accepted ÷ offers that got an answer (accepted or declined) in the period, for FTE openings.
Offers still waiting for an answer are left out.

```sql
select countif(current_stage = 'hired') / count(*) as offer_acceptance_rate
from analytics.fct_application_funnel
where employment_type = 'fte'
  and current_stage in ('hired', 'offer_declined')
  and date(coalesce(hired_at, closed_out_at)) between '2026-07-01' and '2026-09-30'
```

**Example:** Q3 2026 → 40 ÷ (40 + 10) = **80.0%**.

## Funnel conversion by stage

For a cohort of applications, the share that reached each stage out of those that reached the stage
before it. Pick a cohort old enough for every application to have finished: recent applications are
still in progress and make conversion look low.

```sql
select count(*) as applied,
       countif(reached_screen) as screened,
       countif(reached_interview) as interviewed,
       countif(reached_offer) as offered,
       countif(reached_hired) as hired
from analytics.fct_application_funnel
where employment_type = 'fte' and applied_date between '2025-01-01' and '2025-12-31'
```

**Example:** FTE applications submitted in 2025 (all finished):

| Stage | Applications | Conversion from previous stage |
| --- | --- | --- |
| Applied | 11,252 | |
| Screen | 2,833 | 25.2% |
| Interview | 931 | 32.9% |
| Offer | 209 | 22.4% |
| Hired (offer accepted) | 159 | 76.1% |

Overall, 1.4% of applications led to a hire. Group by `source` to compare channels.
