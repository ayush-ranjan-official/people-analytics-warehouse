# Warehouse schema

BigQuery dataset `analytics` (project `people-analytics-demo-2610`). Query the tables below only.
All data is synthetic. Each table states its grain, keys, joins and the traps to avoid.

## dim_date

- Grain: one row per calendar day, 2023-01-01 to 2027-12-31.
- Primary key: date_day.
- Use for: grouping by month, quarter or fiscal period; finding month and quarter ends.
- Join: any date column to date_day; fct_headcount_daily.snapshot_month to month_key.
- Gotcha: the fiscal year runs April to March and is named after the year it ends
  (FY2027 = 2026-04-01 to 2027-03-31).

Table: `analytics.dim_date`

| column | type | description |
| --- | --- | --- |
| date_day | DATE | The calendar day. |
| month_start | DATE | First day of the month. |
| month_end | DATE | Last day of the month. |
| month_key | INT64 | Year and month as an integer, yyyymm (e.g. 202609). |
| calendar_year | INT64 | Calendar year, e.g. 2026. |
| calendar_quarter | INT64 | Calendar quarter, 1 to 4 (Q1 = January to March). |
| year_quarter | STRING | Calendar year and quarter as text, e.g. '2026-Q3'. |
| month_name | STRING | Month name in English, e.g. 'September'. |
| day_name | STRING | Day of the week in English, e.g. 'Monday'. |
| is_weekend | BOOL | True on Saturdays and Sundays. |
| is_month_end | BOOL | True on the last day of a month. |
| is_quarter_end | BOOL | True on the last day of a calendar quarter. |
| fiscal_year | INT64 | Fiscal year (April to March), named after the year it ends. |
| fiscal_quarter | INT64 | Fiscal quarter, 1 to 4 (Q1 = April to June). |

## dim_employee

- Grain: one row per employee per job version (one row per HRIS job-history record).
- Primary key: employee_key.
- Use for: who someone was on a given date (department, manager, level, location, salary); the
  dimension every fact joins to.
- Join: facts carry the employee_key of the version in effect on the event date. For any date d,
  the version is the row where d between valid_from and valid_to.
- Excludes: nothing. All employment types are here; headcount metrics count employment_type = 'fte'.
- Gotcha: a person has several rows. For people employed today filter is_current. A rehired person
  has two employee_ids linked by person_id, so count distinct person_id to count people.
  salary_local is in the row's currency: never sum or average it across currencies.

Table: `analytics.dim_employee`

| column | type | description |
| --- | --- | --- |
| employee_key | INT64 | Surrogate key for this job version (INT64). Facts join on it. |
| job_history_id | STRING | The HRIS job-history record this version comes from. |
| employee_id | STRING | HRIS employee id, e.g. 'E10001'. A person who leaves and is rehired gets a new one. |
| person_id | STRING | The same person across rehires (the employee_id of their first employment record, matched on email). |
| first_name | STRING | First name (synthetic). |
| last_name | STRING | Last name, as it is today (synthetic). |
| email | STRING | Work email, lower case (synthetic). |
| employment_type | STRING | 'fte', 'contractor' or 'intern'. Headcount and attrition count fte only. |
| hire_date | DATE | First day of this employment record. |
| termination_date | DATE | Last day employed on this record. Null while still employed. |
| valid_from | DATE | First day this version applies (inclusive). |
| valid_to | DATE | Last day this version applies (inclusive). 9999-12-31 for a version still in effect. |
| is_latest_version | BOOL | True for the last version of each employment record, including people who have left. |
| is_current | BOOL | True for the version in effect today for people still employed (one row per active employee, all employment types). |
| change_reason | STRING | Why this version started: hire, rehire, promotion, transfer, manager_change, location_change, merit_increase or salary_adjustment. |
| position_id | STRING | The seat held in this version. |
| department_id | STRING | Department or team in this version. Join to dim_org. |
| manager_id | STRING | employee_id of the manager in this version. Null only for the CEO. |
| location | STRING | Work location: Bengaluru, London, New York, Remote or Singapore. |
| job_level | STRING | Job level, 'L1' (entry) to 'L7' (most senior). |
| job_level_number | INT64 | The job level as a number, 1 to 7, for comparisons such as job_level_number >= 5. |
| salary_local | NUMERIC | Annual base salary in the version's currency. |
| currency | STRING | ISO currency of salary_local: INR, GBP, SGD or USD (follows the location). |

## dim_org

- Grain: one row per department or team (11 rows).
- Primary key: department_id.
- Use for: naming and grouping departments; rolling teams up to their division.
- Join: department_id from dim_employee or any fact.
- Gotcha: Engineering is split into three teams (Platform, Data, Security) under a parent row named
  'Engineering' that holds only the head of Engineering. For all of Engineering filter
  division_name = 'Engineering'; department_name = 'Engineering' finds one person.

Table: `analytics.dim_org`

| column | type | description |
| --- | --- | --- |
| department_id | STRING | HRIS department id, e.g. 'D01'. |
| department_name | STRING | Department or team name, e.g. 'Sales' or 'Engineering - Data'. Teams are named '<Department> - <Team>'. |
| source_department_name | STRING | The name as stored in the HRIS, before any correction. |
| name_corrected_in_warehouse | BOOL | True if department_name was corrected by the warehouse's mapping seed. Should be false everywhere; true means the HRIS needs fixing. |
| parent_department_id | STRING | The department a team belongs to. Null for a top-level department. |
| parent_department_name | STRING | Name of the parent department. Null for a top-level department. |
| division_name | STRING | The top-level department this row rolls up to (the parent's name for a team, its own name otherwise). Use it to group a whole function. |
| cost_center | STRING | Finance cost centre code. |
| is_team | BOOL | True if no other department sits under it, i.e. people are assigned here. False only for a parent that groups teams (Engineering). |

## fct_application_funnel

- Grain: one row per application (one candidate applying to one requisition).
- Primary key: application_id.
- Use for: funnel conversion by stage, source effectiveness, offer acceptance, time spent in each
  stage.
- Join: req_id to fct_requisitions; department_id to dim_org; hired_employee_id to
  dim_employee.employee_id.
- Excludes: double submissions (only the first application per candidate and requisition is kept).
- Gotcha: hired_at and reached_hired mean the offer was accepted, not that the person has started
  (fct_hires.hire_date is the start). Contractor and intern openings are included; filter
  employment_type = 'fte' to match fct_hires. For conversion rates use applications old enough to
  have finished, because recent ones are still in progress.

Table: `analytics.fct_application_funnel`

| column | type | description |
| --- | --- | --- |
| application_id | STRING | ATS application id. |
| req_id | STRING | The requisition applied to. |
| candidate_id | STRING | ATS candidate id. The same candidate can apply to several requisitions. |
| department_id | STRING | Hiring department or team. Join to dim_org. |
| source | STRING | Where the candidate came from: Agency, Careers Site, Job Board, LinkedIn or Referral. |
| applied_date | DATE | Day the application was submitted. |
| current_stage | STRING | Latest stage reached. In progress, 'applied', 'screen', 'interview' or 'offer'; finished, 'hired', 'rejected', 'withdrawn' or 'offer_declined'. |
| is_closed | BOOL | True once the application has finished (hired, rejected, withdrawn or offer declined). |
| employment_type | STRING | What the requisition is for: 'fte', 'contractor' or 'intern'. |
| hired_employee_id | STRING | employee_id of the hire, once they have started. Null otherwise. |
| applied_at | TIMESTAMP | When the application was submitted (UTC). |
| screened_at | TIMESTAMP | When it reached the screen stage. Null if it never did. |
| interviewed_at | TIMESTAMP | When it reached the interview stage. Null if it never did. |
| offered_at | TIMESTAMP | When an offer was made. Null if none was. |
| hired_at | TIMESTAMP | When the offer was accepted (not the start date). Null if it wasn't. |
| closed_out_at | TIMESTAMP | When it was rejected, withdrawn or the offer declined. Null if hired or still open. |
| reached_screen | BOOL | True if it reached the screen stage. |
| reached_interview | BOOL | True if it reached the interview stage. |
| reached_offer | BOOL | True if an offer was made. |
| reached_hired | BOOL | True if the offer was accepted. |
| days_applied_to_screen | INT64 | Days from applying to the screen. Null if not screened. |
| days_screen_to_interview | INT64 | Days from the screen to the interview. Null if not interviewed. |
| days_interview_to_offer | INT64 | Days from the interview to the offer. Null if no offer. |
| days_offer_to_hire | INT64 | Days from the offer to its acceptance. Null if not accepted. |

## fct_headcount_daily

- Grain: one row per employment record per day employed, from 2023-10-01 to the reporting date.
- Primary key: snapshot_date + employee_id.
- Use for: headcount on a date; average headcount over a period (the attrition denominator);
  headcount by department, location or level over time.
- Join: employee_key to dim_employee (the version in effect that day); department_id to dim_org;
  snapshot_date to dim_date.date_day; snapshot_month to dim_date.month_key.
- Excludes: days before 2023-10-01, when the history starts, and days after the reporting date.
- Gotcha: contractors and interns are included, so headcount is countif(is_fte). Average headcount
  is FTE rows in the period divided by days in the period, not the headcount at the end. A leaver
  still counts on their last day. The data stops at the HR system's "today"
  (max(snapshot_date), also analytics.int_reporting_date), which is behind the calendar, so
  snapshot_date = current_date() returns nothing. It's a view: always filter on snapshot_date.

Table: `analytics.fct_headcount_daily`

| column | type | description |
| --- | --- | --- |
| snapshot_date | DATE | The day being counted. |
| snapshot_month | INT64 | Month of snapshot_date as yyyymm (e.g. 202609). Joins to dim_date.month_key. |
| employee_key | INT64 | The employee's job version in effect that day. Join to dim_employee. |
| employee_id | STRING | HRIS employee id. |
| person_id | STRING | The same person across rehires. Count distinct person_id to count people rather than records. |
| department_id | STRING | Department or team that day. Join to dim_org. |
| location | STRING | Work location that day. |
| job_level | STRING | Job level that day, 'L1' to 'L7'. |
| job_level_number | INT64 | Job level that day as a number, 1 to 7. |
| employment_type | STRING | 'fte', 'contractor' or 'intern'. |
| is_fte | BOOL | True for full-time employees. Headcount = countif(is_fte). |

## fct_hires

- Grain: one row per FTE hire (an FTE employment record starting) since 2023-10-01.
- Primary key: hire_key.
- Use for: hires by period, department or source; rehire share; time to fill and time to hire
  for people who started.
- Join: employee_key to dim_employee (their first version); req_id to fct_requisitions;
  application_id to fct_application_funnel.
- Excludes: contractors, interns, and people hired before 2023-10-01.
- Gotcha: hire_date is the first day at work. The offer was accepted earlier (offer_accepted_date),
  which is when the recruiting tables count a hire. A rehire counts as a hire (is_rehire).

Table: `analytics.fct_hires`

| column | type | description |
| --- | --- | --- |
| hire_key | INT64 | Surrogate key for the hire (INT64). |
| employee_id | STRING | HRIS employee id of the new employment record. |
| employee_key | INT64 | The hire's first job version. Join to dim_employee. |
| person_id | STRING | The same person across rehires. |
| hire_date | DATE | First day at work. |
| is_rehire | BOOL | True if the person had worked here before (a new employee_id, the same person_id). |
| department_id | STRING | Department or team they joined. Join to dim_org. |
| location | STRING | Work location they joined in. |
| job_level | STRING | Level they joined at, 'L1' to 'L7'. |
| job_level_number | INT64 | Level they joined at, as a number 1 to 7. |
| position_id | STRING | The seat they filled. |
| req_id | STRING | The requisition they were hired through. |
| application_id | STRING | Their application. Join to fct_application_funnel. |
| application_source | STRING | Where the candidate came from: Agency, Careers Site, Job Board, LinkedIn or Referral. |
| req_opened_date | DATE | Day the requisition was opened. |
| offer_accepted_date | DATE | Day the offer was accepted (the requisition's closed_date). |
| days_to_fill | INT64 | Days from the requisition opening to the offer being accepted. |
| days_to_hire | INT64 | Days from the candidate applying to the offer being accepted. |
| days_offer_to_start | INT64 | Days from the offer being accepted to the first day at work. |

## fct_requisitions

- Grain: one row per requisition (an approved opening for one seat).
- Primary key: req_id.
- Use for: open, filled and cancelled requisitions; time to fill; applicants per requisition;
  replacement hiring versus new seats.
- Join: req_id to fct_application_funnel and fct_hires; department_id to dim_org;
  hired_employee_id to dim_employee.employee_id.
- Excludes: nothing. Contractor and intern openings are included; filter employment_type = 'fte'
  to match fct_hires.
- Gotcha: a filled requisition's closed_date is when the offer was accepted, not the start date.

Table: `analytics.fct_requisitions`

| column | type | description |
| --- | --- | --- |
| req_id | STRING | ATS requisition id. |
| position_id | STRING | The seat being hired for. |
| department_id | STRING | Hiring department or team. Join to dim_org. |
| hiring_manager_id | STRING | employee_id of the hiring manager. |
| job_level | STRING | Level being hired for, 'L1' to 'L7'. For a filled requisition, the hire's starting level; otherwise the seat's current grade. |
| job_level_number | INT64 | The same level as a number, 1 to 7. |
| opened_date | DATE | Day the requisition was opened. |
| closed_date | DATE | Day it was filled (offer accepted) or cancelled. Null while open. |
| requisition_status | STRING | 'open', 'filled' or 'cancelled'. |
| employment_type | STRING | What the opening is for: 'fte', 'contractor' or 'intern'. |
| is_backfill | BOOL | True if the seat existed before the requisition opened (replacing someone who left or moved); false for a new seat. |
| days_to_fill | INT64 | Days from opening to the offer being accepted. Null unless filled. |
| applicant_count | INT64 | Number of applications received (duplicates removed). |
| hired_employee_id | STRING | employee_id of the person hired. Null until they start, even if the offer was accepted. |

## fct_terminations

- Grain: one row per FTE exit event.
- Primary key: termination_key.
- Use for: attrition, exits by type or reason, regretted exits, tenure at exit.
- Join: employee_key to dim_employee (the version in effect on the exit date); department_id to
  dim_org.
- Excludes: contractors and interns (a contract or internship ending isn't attrition).
- Gotcha: a rehired person can leave twice under two employee_ids. Attrition counts exit events;
  to count people, count distinct person_id. termination_date is the last day employed, so the
  person leaves headcount the next day.

Table: `analytics.fct_terminations`

| column | type | description |
| --- | --- | --- |
| termination_key | INT64 | Surrogate key for the exit (INT64). |
| termination_id | STRING | HRIS exit record id. |
| employee_id | STRING | HRIS employee id of the employment record that ended. |
| employee_key | INT64 | The job version in effect on the exit date. Join to dim_employee. |
| person_id | STRING | The same person across rehires. |
| termination_date | DATE | Last day employed. |
| termination_type | STRING | 'voluntary' (the person chose to leave), 'involuntary' (the company ended it) or 'retirement'. |
| termination_type_code | STRING | The HRIS code for termination_type, V, I or R. |
| reason_code | STRING | HRIS reason code, e.g. 'VOL_COMPENSATION'. |
| reason | STRING | The reason in plain English, e.g. 'Compensation'. |
| regretted_flag | BOOL | True if the manager marked the leaver as someone they wanted to keep. Set only for voluntary exits; null for involuntary exits and retirements. |
| hire_date | DATE | First day of the employment record that ended. |
| tenure_days_at_exit | INT64 | Days from hire_date to termination_date. Divide by 365.25 for years. |
| department_id | STRING | Department or team on the exit date. Join to dim_org. |
| location | STRING | Work location on the exit date. |
| job_level | STRING | Level on the exit date, 'L1' to 'L7'. |
| job_level_number | INT64 | Level on the exit date, as a number 1 to 7. |
| manager_id | STRING | employee_id of their manager on the exit date. |

---

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
