# Recurring ad-hoc requests, answered once

Eight questions HR asks again and again. Each one used to mean a one-off query. Now each maps to a
tile in the Data Studio dashboard and to an explore in the LookML model ([dashboards.md](dashboards.md)).

The answers below are for the synthetic data through the reporting date, **2026-10-10**. The main
answer to each was computed with the query shown, against the `analytics` marts. The same numbers
come out of the reporting views (`analytics.rpt_*`).

The questions use these periods:

- **Last quarter:** Q3 2026 (2026-07-01 to 2026-09-30).
- **This year:** 2026-01-01 to 2026-10-10, which is 283 days.
- **Today:** the reporting date, 2026-10-10.

Every number is for FTEs only. Rates are for the period and not annualised.

| # | Question | Data Studio tile | LookML explore | Answer |
| --- | --- | --- | --- | --- |
| 1 | How many engineers did we lose last quarter? | Attrition, Division = Engineering | `headcount` | 13 exits, 6.59% |
| 2 | What's our regretted attrition this year by department? | Attrition > Attrition by department | `headcount` | 22 regretted exits, 3.88%; Engineering - Data highest at 8.87% |
| 3 | How long does it take to hire a data engineer? | Recruiting > Time to hire and fill by department | `recruiting` | Median 26 days from application to accepted offer |
| 4 | Which sources bring hires, not just applicants? | Recruiting > Funnel by source | `recruiting` | LinkedIn brings the most; Referral and Agency convert best |
| 5 | What does tenure look like in Sales? | Tenure, Division = Sales | `headcount` | 121 FTEs, average 3.00 years |
| 6 | How many open requisitions does Engineering have? | Not on the dashboard | `recruiting` | 8 |
| 7 | What's headcount in London today vs a year ago? | Workforce > Locations, one-day range | `headcount` | 99 vs 91 |
| 8 | Did offer acceptance drop this quarter? | Recruiting > Offer acceptance by quarter | `recruiting` | No. Q3 rose to 80.0%; Q4 so far 4 of 4 |

## 1. How many engineers did we lose last quarter?

**Data Studio:** Attrition page.

- Set Division to Engineering and the date range to 2026-07-01 to 2026-09-30.
- The Exits and Attrition rate scorecards answer the question.
- Attrition by department splits the exits by team, and Top exit reasons lists why people left.

**LookML:** `headcount` explore.

- Filters: `dim_org.division_name` = Engineering, `dim_date.year_quarter` = 2026-Q3.
- Fields: `fct_terminations.leavers`, `voluntary_leavers` and `regretted_leavers`, then
  `fct_headcount_daily.avg_headcount` and `attrition_rate`.
- For reasons, use the `attrition` explore: `fct_terminations.reason` and `leavers`.

**Answer:** Engineering lost 13 FTEs in Q3 2026, out of an average of 197.3. That is 6.59%, against
4.11% for the company.

- **By type:** 11 left voluntarily and 2 involuntarily. 6 of the 11 were regretted, a regretted rate
  of 3.04% (company 1.71%).
- **By team:**

  | Team | Exits | Voluntary | Regretted |
  | --- | --- | --- | --- |
  | Engineering - Platform | 8 | 7 | 4 |
  | Engineering - Data | 4 | 3 | 2 |
  | Engineering - Security | 1 | 1 | 0 |

- **Top reasons:** Compensation (4) and Better opportunity (3).
- **Q2 2026 for comparison:** 10 exits, 5.26%.

```sql
with leavers as (
    select count(*) as exits,
           countif(termination_type = 'voluntary') as voluntary,
           countif(regretted_flag) as regretted
    from analytics.fct_terminations
    join analytics.dim_org using (department_id)
    where division_name = 'Engineering'
      and termination_date between '2026-07-01' and '2026-09-30'
),
headcount as (
    select count(*) / count(distinct snapshot_date) as average_headcount
    from analytics.fct_headcount_daily
    join analytics.dim_org using (department_id)
    where is_fte and division_name = 'Engineering'
      and snapshot_date between '2026-07-01' and '2026-09-30'
)
select exits, voluntary, regretted, round(average_headcount, 1) as average_headcount,
       round(100 * exits / average_headcount, 2) as attrition_rate_pct
from leavers, headcount
```

## 2. What's our regretted attrition this year by department?

**Data Studio:** Attrition page. Set the date range to 2026-01-01 to 2026-10-10. Read the Attrition by
department table, sorted by Regretted attrition rate.

**LookML:** `headcount` explore.

- Filter: `fct_headcount_daily.snapshot_date` from 2026-01-01 to 2026-10-10.
- Fields: `dim_org.department_name`, `fct_terminations.regretted_leavers`,
  `fct_terminations.voluntary_leavers`, `fct_headcount_daily.avg_headcount` and
  `regretted_attrition_rate`.

**Answer:** company-wide, 22 regretted exits over an average of 566.5 FTEs, which is 3.88% for the
283 days. There were 68 exits in all, 59 of them voluntary. Regretted exits sit mostly in Engineering:

| Department | Average headcount | Exits | Voluntary | Regretted | Regretted rate |
| --- | --- | --- | --- | --- | --- |
| Engineering - Data | 56.4 | 9 | 7 | 5 | 8.87% |
| Engineering - Platform | 99.5 | 16 | 14 | 7 | 7.03% |
| Finance | 41.5 | 2 | 2 | 2 | 4.82% |
| Product | 52.5 | 7 | 6 | 2 | 3.81% |
| Sales | 115.7 | 14 | 13 | 4 | 3.46% |
| Operations | 71.6 | 8 | 7 | 2 | 2.79% |

Engineering - Security, Marketing, Legal and People had voluntary exits this year, but none were
regretted.

"This year" could also mean the fiscal year. For FY2027 to date, start the range at 2026-04-01.

```sql
with leavers as (
    select department_id, count(*) as exits,
           countif(termination_type = 'voluntary') as voluntary,
           countif(regretted_flag) as regretted
    from analytics.fct_terminations
    where termination_date between '2026-01-01' and '2026-10-10'
    group by department_id
),
headcount as (
    select department_id, count(*) / count(distinct snapshot_date) as average_headcount
    from analytics.fct_headcount_daily
    where is_fte and snapshot_date between '2026-01-01' and '2026-10-10'
    group by department_id
)
select department_name, round(average_headcount, 1) as average_headcount,
       coalesce(exits, 0) as exits, coalesce(voluntary, 0) as voluntary,
       coalesce(regretted, 0) as regretted,
       round(100 * coalesce(regretted, 0) / average_headcount, 2) as regretted_rate_pct
from headcount
join analytics.dim_org using (department_id)
left join leavers using (department_id)
order by regretted_rate_pct desc, department_name
```

## 3. How long does it take to hire a data engineer?

**Data Studio:** Recruiting page. Set the date range to 2025-10-01 to 2026-09-30; this table filters
on the date the offer was accepted. Read the Engineering - Data row of Time to hire and fill by
department.

**LookML:** `recruiting` explore.

- Filters: `dim_org.department_name` = Engineering - Data, `fct_application_funnel.hired_at_date`
  (Offer Accepted Date) in the range.
- Fields: `fct_application_funnel.offers_accepted`, `median_time_to_hire_days` and
  `avg_time_to_hire_days`.
- For time to fill, filter `fct_requisitions.closed_date` instead and use
  `fct_requisitions.avg_days_to_fill`. It's a mean only in LookML.

**Answer:** about four weeks from application to accepted offer. Over the last four complete
quarters, 9 data engineers accepted offers:

- **Time to hire:** median 26 days, mean 27.1, ranging from 17 to 37. The company median is 26 days
  (160 offers).
- **Time to fill** for the same team: median 35 days, mean 41.4, from opening the requisition to the
  accepted offer.

Nine is a small sample, so read it as a range rather than a target.

```sql
select count(*) as offers_accepted,
       round(avg(date_diff(date(hired_at), applied_date, day)), 1) as average_days,
       approx_quantiles(date_diff(date(hired_at), applied_date, day), 2)[offset(1)] as median_days
from analytics.fct_application_funnel
join analytics.dim_org using (department_id)
where employment_type = 'fte' and reached_hired
  and department_name = 'Engineering - Data'
  and date(hired_at) between '2025-10-01' and '2026-09-30'
```

```sql
select count(*) as requisitions_filled,
       round(avg(days_to_fill), 1) as average_days,
       approx_quantiles(days_to_fill, 2)[offset(1)] as median_days
from analytics.fct_requisitions
join analytics.dim_org using (department_id)
where employment_type = 'fte' and requisition_status = 'filled'
  and department_name = 'Engineering - Data'
  and closed_date between '2025-10-01' and '2026-09-30'
```

## 4. Which sources bring hires, not just applicants?

**Data Studio:** Recruiting page. Set the date range to 2025-01-01 to 2025-12-31; the two funnel
tables filter on the application date.

- Funnel by source gives the counts at each stage.
- Conversion by stage gives the rate at each step.

**LookML:** `recruiting` explore.

- Filter: `fct_application_funnel.applied_date` in 2025.
- Fields: `fct_application_funnel.source`, `applications`, `offers_accepted` and
  `funnel_conversion_rate`, and `fct_hires.hires` for the people who started.

**Answer:** the 2025 cohort is a fair test, because all 11,252 FTE applications from 2025 have
finished.

| Source | Applications | Screened | Interviewed | Offered | Accepted | Started | Application to hire |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LinkedIn | 3,619 | 850 | 290 | 74 | 57 | 57 | 1.58% |
| Careers Site | 3,312 | 714 | 226 | 50 | 34 | 34 | 1.03% |
| Referral | 1,151 | 604 | 200 | 32 | 29 | 29 | 2.52% |
| Agency | 902 | 358 | 120 | 29 | 22 | 22 | 2.44% |
| Job Board | 2,268 | 307 | 95 | 24 | 17 | 17 | 0.75% |

- **LinkedIn** brings the most hires (57 of 159), on volume.
- **Referrals and agencies** convert best. Each is about 2.5% of applications to hire, against 1.4%
  overall, and most of the difference is at the screen.
- **Job boards** bring the third-most applications but the fewest hires: 2,268 applications for 17.
- Every accepted offer from 2025 has started.

```sql
select source,
       count(*) as applications,
       countif(reached_screen) as screened,
       countif(reached_interview) as interviewed,
       countif(reached_offer) as offered,
       countif(reached_hired) as accepted,
       countif(hired_employee_id is not null) as started,
       round(100 * countif(reached_hired) / count(*), 2) as application_to_hire_pct
from analytics.fct_application_funnel
where employment_type = 'fte' and applied_date between '2025-01-01' and '2025-12-31'
group by source
order by accepted desc
```

## 5. What does tenure look like in Sales?

**Data Studio:** Tenure page, with Division set to Sales.

- Average tenure today reads the reporting date.
- FTE headcount by tenure band at each month end shows the trend.
- Attrition by tenure band covers the selected period.

**LookML:** `headcount` explore.

- Filters: `dim_org.division_name` = Sales, `fct_headcount_daily.snapshot_date` = 2026-10-10.
- Fields: `fct_headcount_daily.tenure_band`, `headcount`, `avg_tenure_years` and
  `median_tenure_years`.

**Answer:** on 2026-10-10, Sales has 121 FTEs with an average tenure of 3.00 years (median 2.5). That
is longer than the company's 2.77 years.

| Under 1 year | 1-3 years | 3-5 years | 5+ years |
| --- | --- | --- | --- |
| 22 | 47 | 27 | 25 |

New joiners leave fastest. Over the last four complete quarters, Sales attrition by tenure band was:

| Under 1 year | 1-3 years | 3-5 years | 5+ years |
| --- | --- | --- | --- |
| 21.3% | 14.6% | 8.9% | 6.1% |

The company follows the same pattern, at 21.3%, 14.2%, 9.6% and 7.7%.

```sql
select count(*) as headcount,
       round(avg(tenure_days) / 365.25, 2) as average_years,
       round(approx_quantiles(tenure_days, 2)[offset(1)] / 365.25, 2) as median_years,
       countif(tenure_days < 365.25) as under_1_year,
       countif(tenure_days >= 365.25 and tenure_days < 3 * 365.25) as years_1_to_3,
       countif(tenure_days >= 3 * 365.25 and tenure_days < 5 * 365.25) as years_3_to_5,
       countif(tenure_days >= 5 * 365.25) as years_5_plus
from (
    select date_diff(h.snapshot_date, e.hire_date, day) as tenure_days
    from analytics.fct_headcount_daily as h
    join analytics.dim_employee as e using (employee_key)
    join analytics.dim_org as o on h.department_id = o.department_id
    where h.is_fte and o.division_name = 'Sales' and h.snapshot_date = '2026-10-10'
)
```

Attrition by tenure band, for Sales and the company. Each exit counts at its tenure on the last day:

```sql
with days as (
    select h.snapshot_date, o.division_name,
           date_diff(h.snapshot_date, e.hire_date, day) as tenure_days, 1 as fte_day, 0 as exits
    from analytics.fct_headcount_daily as h
    join analytics.dim_employee as e using (employee_key)
    join analytics.dim_org as o on h.department_id = o.department_id
    where h.is_fte and h.snapshot_date between '2025-10-01' and '2026-09-30'
    union all
    select t.termination_date, o.division_name, t.tenure_days_at_exit, 0, 1
    from analytics.fct_terminations as t
    join analytics.dim_org as o using (department_id)
    where t.termination_date between '2025-10-01' and '2026-09-30'
),
banded as (
    select *,
           case when tenure_days < 365.25 then 'Under 1 year'
                when tenure_days < 3 * 365.25 then '1-3 years'
                when tenure_days < 5 * 365.25 then '3-5 years'
                else '5+ years' end as tenure_band
    from days
)
select tenure_band,
       round(100 * sum(if(division_name = 'Sales', exits, 0))
             / (sum(if(division_name = 'Sales', fte_day, 0)) / 365), 1) as sales_pct,
       round(100 * sum(exits) / (sum(fte_day) / 365), 1) as company_pct
from banded
group by tenure_band
order by min(tenure_days)
```

## 6. How many open requisitions does Engineering have?

**Data Studio:** not on the dashboard. The reporting views hold applications and exits, not the
status of each requisition (see Limitations in [dashboards.md](dashboards.md)).

**LookML:** `recruiting` explore.

- Filter: `dim_org.division_name` = Engineering. The explore's employment type filter stays at fte.
- Fields: `dim_org.department_name` and `fct_requisitions.open_requisitions`.
- The explore reaches requisitions through their applications. Today every open requisition has
  applications, so the count is complete.

**Answer:** 8 open FTE requisitions, out of 15 company-wide.

| Team | Open requisitions |
| --- | --- |
| Engineering - Platform | 5 |
| Engineering - Data | 2 |
| Engineering - Security | 1 |

- **Backfills:** 6 of the 8 replace someone who left or moved.
- **Age:** they've been open 32 days on average. The oldest, in Platform, opened on 2026-07-07, 95
  days ago.

```sql
select o.department_name,
       count(*) as open_requisitions,
       countif(r.is_backfill) as backfills,
       min(r.opened_date) as oldest_opened,
       round(avg(date_diff(d.reporting_date, r.opened_date, day))) as average_days_open
from analytics.fct_requisitions as r
join analytics.dim_org as o using (department_id)
cross join analytics.int_reporting_date as d
where r.employment_type = 'fte' and r.requisition_status = 'open'
  and o.division_name = 'Engineering'
group by o.department_name
order by o.department_name
```

## 7. What's headcount in London today vs a year ago?

**Data Studio:** Workforce page.

- Set the date range to the single day 2026-10-10 to 2026-10-10.
- Read the London row of the Locations table. Over one day, Average headcount is that day's
  headcount.
- The table's Previous year comparison shows 2025-10-10 alongside.

**LookML:** `headcount` explore.

- Filters: `fct_headcount_daily.location` = London and `fct_headcount_daily.snapshot_date` on a
  single day. Run it once for 2026-10-10 and once for 2025-10-10.
- Field: `fct_headcount_daily.headcount`.

**Answer:** London has 99 FTEs today, against 91 a year ago, up 8 (8.8%).

- **Including contractors and interns:** 103, against 97.
- **What changed:** 21 FTEs started in London over the year and 14 left. People moving location
  account for the other one.
- **The company** grew faster, from 516 to 596 FTEs (15.5%).

```sql
select snapshot_date, countif(is_fte) as fte, count(*) as all_employment_types
from analytics.fct_headcount_daily
where location = 'London' and snapshot_date in ('2025-10-10', '2026-10-10')
group by snapshot_date
order by snapshot_date
```

## 8. Did offer acceptance drop this quarter?

**Data Studio:** Recruiting page. Set the date range to 2025-10-01 to 2026-10-10; Offer acceptance by
quarter filters on the date the candidate answered.

**LookML:** `recruiting` explore.

- Filter: `fct_application_funnel.offer_answered_date` in the range.
- Fields: `fct_application_funnel.offer_answered_quarter`, `offers_accepted`, `offers_declined` and
  `acceptance_rate`.

**Answer:** no.

- **This quarter (Q4 2026):** 10 days in, 4 offers have been answered and all 4 were accepted. One
  more is waiting for an answer. That is too few to call a trend.
- **Last complete quarter:** acceptance rose to 80.0% in Q3, from 69.1% in Q2. Q2 was the lowest of
  the last four quarters, with 21 declines.

| Quarter | Accepted | Declined | Acceptance |
| --- | --- | --- | --- |
| 2025-Q4 | 37 | 12 | 75.5% |
| 2026-Q1 | 36 | 13 | 73.5% |
| 2026-Q2 | 47 | 21 | 69.1% |
| 2026-Q3 | 40 | 10 | 80.0% |
| 2026-Q4 (to 2026-10-10) | 4 | 0 | 100.0% |

```sql
select d.year_quarter,
       countif(a.current_stage = 'hired') as accepted,
       countif(a.current_stage = 'offer_declined') as declined,
       round(100 * countif(a.current_stage = 'hired') / count(*), 1) as acceptance_pct
from analytics.fct_application_funnel as a
join analytics.dim_date as d on date(coalesce(a.hired_at, a.closed_out_at)) = d.date_day
where a.employment_type = 'fte' and a.current_stage in ('hired', 'offer_declined')
  and d.date_day >= '2025-10-01'
group by d.year_quarter
order by d.year_quarter
```
