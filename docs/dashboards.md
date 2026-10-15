# Dashboards

Two BI layers read the same `analytics` dataset, and both use the formulas in
[metric-definitions.md](metric-definitions.md).

| | LookML | Data Studio |
| --- | --- | --- |
| What | The semantic layer: views, explores, measures and one dashboard, in [lookml/](../lookml/) | The live dashboard "HR Overview - Nodewell Labs (synthetic data)" |
| Reads | The marts | Three reporting views, `analytics.rpt_*` ([dbt/models/reporting/](../dbt/models/reporting/)) |
| Runs on | Nothing yet. Looker has no free tier ([D7](decisions.md)), so it's validated offline with `make validate-lookml` | Data Studio (free), against the BigQuery sandbox |
| Good for | Any question the explores can answer. Looker handles the joins and fanout | The fixed pages and tiles below |

LookML is where each metric is defined once and reviewed in git. A company that runs Looker would
deploy it as it is. Data Studio is the free tool that shows the numbers live. It can't join
tables safely: there's no fanout protection and no symmetric aggregates. So dbt shapes three reporting
views for it, and the logic stays in the warehouse, where it's tested. The dbt exposure `hr_overview`
puts the dashboard in the lineage: `dbt ls --select +exposure:hr_overview` lists every model it
depends on.

Both layers give the same Q3 2026 numbers for the company and for a division (see
[Verification](#verification)). The few places where they differ are listed at the end of this page.

## Data Studio: HR Overview

Four pages: Workforce, Attrition, Tenure and Recruiting. The build takes about 20 minutes.

### Before you start

- The views must exist in `analytics`:
  ```sql
  select table_name from analytics.INFORMATION_SCHEMA.TABLES where table_name like 'rpt%'
  ```
  Expect `rpt_exits`, `rpt_recruiting_funnel` and `rpt_workforce_daily`. If they're missing, the
  nightly run creates them, or run a prod `dbt build --select reporting`. They're views, so creating
  them writes no storage.
- Sign in to Data Studio with the Google account that owns `people-analytics-demo-2610`, not the dbt
  service account.
- The project has no billing account ([D1](decisions.md)). Data Studio's BigQuery help says a billing
  project is needed. The sandbox's free quotas should cover the queries, but this hasn't been tried
  here yet. If Data Studio asks for a billing account, stop: billing is never turned on for this
  project. Record the result in [platform-notes.md](platform-notes.md) either way.

### 1. Connect the views (3 min)

1. In Data Studio, **Create** > **Report**. Under **Add data to report**, choose the **BigQuery**
   connector and authorize it.
2. **My projects** > `people-analytics-demo-2610` > `analytics` > `rpt_workforce_daily` > **Add**,
   then **Add to report**.
3. Add `rpt_exits` and `rpt_recruiting_funnel` the same way (**Add data** in the toolbar).
4. Rename the three data sources `Workforce daily`, `Exits` and `Recruiting funnel` (Resource >
   Manage added data sources > Edit; the name is at the top left).
5. In each data source, leave **Data credentials** on **Owner's credentials** (viewers then need no
   BigQuery access) and **Data freshness** on the default 12 hours. The marts change once a night,
   so a shorter interval only adds queries.

Check the field types Data Studio picked. Every `*_date` field and `month_start` should be Date. The
`is_*` fields and `regretted_flag` should be Boolean. The count fields should be Number with
aggregation Sum: headcount, total_tenure_days, hires, the five `*exits` fields, screened, interviewed,
offered, hired and offer_declined. These counts are the only fields that should ever be summed.

### 2. Match the filter fields across sources (2 min)

A Division or Location control only filters charts from another BigQuery source if the field IDs
match; a matching name isn't enough. Set the IDs before you build any chart, because changing an ID
that a chart already uses breaks that chart.

Resource > **Manage field names and IDs** > **Add field override**, once per row:

| Data source | Field | New ID |
| --- | --- | --- |
| Workforce daily | division_name | `division` |
| Exits | division_name | `division` |
| Recruiting funnel | division_name | `division` |
| Workforce daily | location | `work_location` |
| Exits | location | `work_location` |

Applications have no work location, so the Location control has no effect on the Recruiting page.

### 3. Calculated fields (4 min)

Add these in the data source rather than in a chart, so every chart uses the same definition:
Resource > Manage added data sources > Edit > **Add a field**. Give each one the type shown.

**Workforce daily**

| Field | Formula | Type |
| --- | --- | --- |
| Days in period | `COUNT_DISTINCT(snapshot_date)` | Number |
| Average headcount | `SUM(headcount) / COUNT_DISTINCT(snapshot_date)` | Number |
| Attrition rate | `SUM(exits) / (SUM(headcount) / COUNT_DISTINCT(snapshot_date))` | Percent |
| Voluntary attrition rate | `SUM(voluntary_exits) / (SUM(headcount) / COUNT_DISTINCT(snapshot_date))` | Percent |
| Involuntary attrition rate | `SUM(involuntary_exits) / (SUM(headcount) / COUNT_DISTINCT(snapshot_date))` | Percent |
| Regretted attrition rate | `SUM(regretted_exits) / (SUM(headcount) / COUNT_DISTINCT(snapshot_date))` | Percent |
| Average tenure (years) | `SUM(total_tenure_days) / SUM(headcount) / 365.25` | Number |

- Days in period isn't on any tile. It's a check: under any filter, it should equal the number of
  days in the date range that fall between 2023-10-01 and the reporting date. A range that runs past
  the reporting date, such as Last 30 days or This quarter, counts only the days with data.
- Over a single day, Average headcount is that day's headcount.
- Average tenure only means something on a single day. The tiles that use it are pinned to one day.

**Exits**

| Field | Formula | Type |
| --- | --- | --- |
| Exits | `COUNT(termination_key)` | Number |
| Average tenure at exit (years) | `AVG(tenure_years_at_exit)` | Number |

**Recruiting funnel**

| Field | Formula | Type |
| --- | --- | --- |
| Applications | `COUNT(application_id)` | Number |
| Offers accepted | `SUM(hired)` | Number |
| Offers declined | `SUM(offer_declined)` | Number |
| Screen rate | `SUM(screened) / COUNT(application_id)` | Percent |
| Interview rate | `SUM(interviewed) / SUM(screened)` | Percent |
| Offer rate | `SUM(offered) / SUM(interviewed)` | Percent |
| Offer to hire rate | `SUM(hired) / SUM(offered)` | Percent |
| Application to hire rate | `SUM(hired) / COUNT(application_id)` | Percent |
| Offer acceptance rate | `SUM(hired) / (SUM(hired) + SUM(offer_declined))` | Percent |
| Average time to hire (days) | `AVG(days_to_hire)` | Number |
| Median time to hire (days) | `MEDIAN(days_to_hire)` | Number |
| Average time to fill (days) | `AVG(days_to_fill)` | Number |
| Median time to fill (days) | `MEDIAN(days_to_fill)` | Number |

- Offer to hire rate and offer acceptance rate are different things:
  - Offer to hire rate: of the offers made to a cohort of applications, the share accepted.
  - Offer acceptance rate: of the offers answered in a period, the share accepted.
- `days_to_fill` is set only on the accepted application, so each filled requisition counts once.
- On BigQuery, Data Studio's MEDIAN uses `APPROX_QUANTILES`, the same function the metric definitions
  use. Time to fill in Q3 2026 reads 47 days. An interpolated median would say 49.

### 4. Title and controls (3 min)

On the first page:

- Add a text box with the title `HR Overview - Nodewell Labs (synthetic data)`. Under it, in smaller
  text: `Synthetic data. FTEs only. The data runs to the HR system's reporting date, a few days behind
  the calendar. Rates are for the selected period and not annualised.`
- Add a **Date range control**. Set its default date range to Custom, 2025-10-01 to 2026-09-30: the
  last four complete quarters. A partial quarter understates its rate. Move the range forward once
  each quarter has closed.
- Add a **Drop-down list** control on `division_name` (Workforce daily), titled Division.
- Add a **Drop-down list** control on `location` (Workforce daily), titled Location.

Select the title, the note and the three controls, then right-click > **Make report-level**. They
then appear on every page and filter every chart.

### 5. Pages and tiles (8 min)

For each chart, pick the data source and set **Setup > Date range dimension** as listed. Chart
filters are under Setup > Filter > Add a filter. Show rates as percentages with 2 decimals (1 for
recruiting), and headcount and days with 1 decimal.

**Page 1: Workforce.** Source: Workforce daily. Date range dimension: `snapshot_date`.

| Tile | Chart | Dimension | Metrics | Sort | Filter and notes |
| --- | --- | --- | --- | --- | --- |
| FTE headcount today | Scorecard | | headcount | | Filter `is_reporting_date` = true. Set the chart's own default date range to a preset such as Last 30 days. A chart's own range overrides the date range control, and any range that contains the reporting date works, because the filter picks the day. |
| Average headcount | Scorecard | | Average headcount | | |
| FTE starts | Scorecard | | hires | | First days at work in the range |
| Exits | Scorecard | | exits | | |
| Attrition rate | Scorecard | | Attrition rate | | |
| Average FTE headcount by month | Time series | month_start (type Year Month) | Average headcount | month_start, ascending | |
| Average headcount by department | Bar | department_name | Average headcount | Average headcount, descending | 'Engineering' alone is the parent row with one person. Use Division for all of Engineering |
| Locations | Table | location | Average headcount, hires, exits, Attrition rate | Average headcount, descending | Comparison date range: Previous year |

**Page 2: Attrition.** Source: Workforce daily, date range dimension `snapshot_date`, except where
noted.

| Tile | Chart | Dimension | Metrics | Sort | Filter and notes |
| --- | --- | --- | --- | --- | --- |
| Exits | Scorecard | | exits | | |
| Attrition rate | Scorecard | | Attrition rate | | |
| Voluntary attrition rate | Scorecard | | Voluntary attrition rate | | |
| Regretted attrition rate | Scorecard | | Regretted attrition rate | | |
| Quarterly attrition rate | Column | year_quarter | Attrition rate, Voluntary attrition rate, Involuntary attrition rate | year_quarter, ascending | Retirements count in the total only. A quarter that the date range cuts short shows a partial rate |
| Attrition by department | Table | department_name | Average headcount, exits, voluntary_exits, regretted_exits, Attrition rate, Regretted attrition rate | Attrition rate, descending | |
| Top exit reasons | Bar (horizontal) | reason | Exits | Exits, descending | Source: Exits. Date range dimension: `termination_date`. 10 bars |

**Page 3: Tenure.** Source: Workforce daily, date range dimension `snapshot_date`, except where noted.

| Tile | Chart | Dimension | Metrics | Sort | Filter and notes |
| --- | --- | --- | --- | --- | --- |
| Average tenure today (years) | Scorecard | | Average tenure (years) | | Same filter and own date range as FTE headcount today |
| Tenure bands at each month end | Stacked column | month_start (type Year Month); breakdown dimension tenure_band | headcount | month_start, ascending; breakdown sorted by tenure_band_order (Max), ascending | Filter `is_month_end` = true |
| Attrition by tenure band | Table | tenure_band | Average headcount, exits, Attrition rate | tenure_band_order (Max), ascending | Tenure as of each day. An exit counts at its tenure on the last day |
| Average tenure at exit (years) | Scorecard | | Average tenure at exit (years) | | Source: Exits. Date range dimension: `termination_date` |

Text sorts '< 1 year' after '5+ years', so always sort the bands by `tenure_band_order`.

**Page 4: Recruiting.** Source: Recruiting funnel. Add a page filter (Page > Current page settings >
Setup > Filter > Add a filter): Include `employment_type` = `fte`. If your version has no page
filter, add the same filter to each chart. Each recruiting metric is dated differently, so set the
date range dimension per chart.

| Tile | Chart | Dimension | Metrics | Sort | Date range dimension and notes |
| --- | --- | --- | --- | --- | --- |
| Offers accepted | Scorecard | | Offers accepted | | `offer_accepted_date` |
| Median time to hire (days) | Scorecard | | Median time to hire (days) | | `offer_accepted_date` |
| Median time to fill (days) | Scorecard | | Median time to fill (days) | | `offer_accepted_date` |
| Offer acceptance rate | Scorecard | | Offer acceptance rate | | `offer_decision_date` |
| Time to hire and fill by department | Table | department_name | Offers accepted, Median time to hire, Average time to hire, Median time to fill, Average time to fill | Median time to hire, descending | `offer_accepted_date`. Summary row on |
| Funnel by source | Table | source | Applications, screened, interviewed, offered, Offers accepted, Application to hire rate | Offers accepted, descending | `applied_date`. Summary row on: it is the funnel |
| Conversion by stage | Table | source | Screen rate, Interview rate, Offer rate, Offer to hire rate | Offer to hire rate, descending | `applied_date`. Summary row on: each rate recomputed from the totals |
| Offer acceptance by quarter | Table | offer_decision_date (type Year Quarter) | Offers accepted, Offers declined, Offer acceptance rate | offer_decision_date, ascending | `offer_decision_date` |

The funnel tiles count applications submitted in the date range. Recent applications are still in
progress and make conversion look low. For final rates, pick a range that ended a few months ago,
such as 2025.

### Why the sources hold counts only

Data Studio aggregates one source's rows after the filters apply, and its blends have no fanout
protection, so each chart reads a single view. Two rules follow.

**A rate can't be added up.** If a view stored a rate, Data Studio would sum or average it across
whatever rows a filter leaves. Take Engineering in Q3 2026. Its four department rows have rates of
0%, 7.07%, 7.86% and 2.63%, which average 4.39%. The division's rate is 6.59%: 13 exits over an
average 197.3 FTEs. So the views store counts (FTE days, exits by type, starts and tenure days), and
every rate is a calculated field. Each one divides sums at query time, after the filters apply.

**The day count must not depend on the filter.** Average headcount is FTE days divided by days in
the period. Data Studio can only count the days as `COUNT_DISTINCT(snapshot_date)` over the rows a
filter leaves. A view with rows only where someone works drops the days when nobody matched. One
small slice in Q3 2026 shows the effect: Engineering - Data, Singapore, under a year's tenure. People
were in that slice on 49 of the quarter's 92 days, for 98 FTE days. A sparse view would give an
average headcount of 2.0 instead of 1.07, and understate any rate on that slice by almost half.

- `rpt_workforce_daily` has a row for every day, department, location and tenure band, with zeros
  where nobody matches: 1,106 days x 220 combinations = 243,320 rows. So the day count is always the
  number of days in the range.
- Never filter out the zero rows, for example with headcount > 0.
- The dbt test `assert_rpt_workforce_daily_is_dense` checks that every day has all 220 combinations.
- `assert_rpt_workforce_reconciles` checks that the view's totals equal the marts.
- `assert_rpt_workforce_matches_marts_by_cell` checks the same cell by cell: each exit and start sits
  in the day, department, location and tenure band of the person's own headcount row.

### Verification

Set the date range to 2026-07-01 to 2026-09-30 and leave Division and Location empty. With data
through 2026-10-10, the tiles must show:

| Page | Tile | Expected |
| --- | --- | --- |
| Workforce | Average headcount | 584.4 |
| Workforce | FTE starts | 45 |
| Workforce | Exits | 24 |
| Workforce | Attrition rate | 4.11% |
| Attrition | Voluntary attrition rate | 3.42% |
| Attrition | Regretted attrition rate | 1.71% |
| Attrition | Quarterly attrition rate, 2026-Q3 | 4.11%, 3.42%, 0.51% |
| Tenure | Average tenure at exit | 2.03 years |
| Recruiting | Offers accepted | 40 |
| Recruiting | Median time to hire, median time to fill | 25 days, 47 days |
| Recruiting | Offer acceptance rate | 80.0% |
| Recruiting | Time to hire and fill by department, summary row | average 26.2 days to hire, 52.6 to fill |

Then check the following:

- **Division = Engineering:** Average headcount 197.3, Exits 13, Attrition rate 6.59%.
- **Date range 2025-01-01 to 2025-12-31, Division cleared:** the Funnel by source summary row reads
  11,252 applications, then 2,833 screened, 931 interviewed, 209 offered and 159 accepted.
- **The two "today" tiles** move every night. On reporting date 2026-10-10 they read 596 FTEs and
  2.77 years.

This SQL computes the Workforce and Attrition tiles from the same view, for all divisions and for
each division:

```sql
select
    if(grouping(division_name) = 1, '(all divisions)', division_name) as division,
    count(distinct snapshot_date) as days,
    round(sum(headcount) / count(distinct snapshot_date), 1) as average_headcount,
    sum(exits) as exits,
    round(100 * sum(exits) / (sum(headcount) / count(distinct snapshot_date)), 2) as attrition_rate_pct,
    round(100 * sum(voluntary_exits) / (sum(headcount) / count(distinct snapshot_date)), 2) as voluntary_pct,
    round(100 * sum(regretted_exits) / (sum(headcount) / count(distinct snapshot_date)), 2) as regretted_pct
from analytics.rpt_workforce_daily
where snapshot_date between '2026-07-01' and '2026-09-30'
group by grouping sets ((), (division_name))
order by grouping(division_name) desc, division
```

The first rows are `(all divisions)`: 92 days, 584.4, 24 exits, 4.11%, 3.42%, 1.71%. Then
`Engineering`: 92 days, 197.3, 13 exits, 6.59%. The Recruiting scorecards:

```sql
select
    sum(hired) as offers_accepted,
    round(avg(days_to_hire), 1) as average_time_to_hire,
    approx_quantiles(days_to_hire, 2)[offset(1)] as median_time_to_hire,
    round(avg(days_to_fill), 1) as average_time_to_fill,
    approx_quantiles(days_to_fill, 2)[offset(1)] as median_time_to_fill
from analytics.rpt_recruiting_funnel
where employment_type = 'fte' and offer_accepted_date between '2026-07-01' and '2026-09-30'
```

It returns 40, 26.2, 25, 52.6 and 47. These are the numbers the mart queries in
[metric-definitions.md](metric-definitions.md) give. Both queries were run against the dev build of
the views (`dbt_dev`).

### Cost hygiene

- **Every chart is its own BigQuery query.** It runs whenever the chart renders with a page, filter
  and date range it hasn't cached. Each query counts against the sandbox's 1 TiB a month of free
  query processing. The nightly dbt run draws on the same allowance.
- **Measured costs:**
  - A chart on Workforce daily processes about 2 MB but is billed about 100 MB. BigQuery bills at
    least 10 MB for each table a query reads, and that view reads about ten.
  - Charts on Exits and Recruiting funnel are billed about 30 MB each.
  - One uncached pass through all four pages is about 2 GB, so 1 TiB covers roughly 500 full passes
    a month.
- **Caching:** Data Studio keeps each result for the data freshness interval (12 hours). Reopening
  the report with the same filters costs nothing. Each new filter value or date range sends new
  queries.
- **So:**
  - Share the report with a few named people, not "anyone with the link", and don't embed or
    publish it.
  - Leave Viewer data refresh off (the default), and don't click Refresh data again and again.
- **Check usage** in the BigQuery console, signed in as the project owner. The dbt service account
  can't list other users' jobs. Data Studio labels its queries `requestor:looker_studio`:

  ```sql
  select count(*) as queries, round(sum(total_bytes_billed) / pow(1024, 3), 1) as gib_billed
  from `region-asia-south1`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  where creation_time >= timestamp_trunc(current_timestamp(), month)
    and exists (select 1 from unnest(labels) where key = 'requestor' and value = 'looker_studio')
  ```

  Drop the last line to see everything the project has run this month.

### Known gaps

- **Open requisitions aren't on the dashboard.** The views hold flows (applications, exits), while open
  requisitions are a count as of today. Use the LookML recruiting explore or the SQL in
  [ad-hoc-requests.md](ad-hoc-requests.md). A reporting view with one row per requisition would add it.
- **The Recruiting page ignores Location.**
- **The default date range is fixed.** Move it forward each quarter.
- **The two "today" tiles can go blank.** They depend on their own date range containing the reporting
  date. If the nightly run stops for longer than that range, they show nothing.

## LookML

### Files

| File | Contents |
| --- | --- |
| `lookml/manifest.lkml` | Constants `bq_project` and `bq_dataset`. Set `bq_dataset` to `dbt_dev` to point every view at the dev build |
| `lookml/people_analytics.model.lkml` | The connection and includes, the `nightly_build` datagroup, the `can_see_salary` access grant and the three explores. The datagroup keeps results cached until the nightly rebuild changes the marts, for 24 hours at most. Its trigger reads table metadata, which BigQuery doesn't bill |
| `lookml/views/*.view.lkml` | One view per mart (8). Every measure has a description: its formula, plus the worked example from the metric definitions for the standard metrics |
| `lookml/dashboards/hr_overview.dashboard.lookml` | A one-page LookML version of the dashboard over the same metrics: 8 tiles, Division, Location and Date range filters, and by default the four quarters to 2026-09-30 |

### Explores

| Explore | Base view | Joins | Use for |
| --- | --- | --- | --- |
| `headcount` | fct_headcount_daily | dim_employee, dim_org, dim_date (many_to_one); fct_terminations (one_to_one, measures only) | Headcount, average headcount, attrition rates and tenure. The fact is a BigQuery view, so a snapshot date filter is required (default: the current quarter and the four before it) unless a calendar quarter, calendar year or fiscal year is filtered |
| `attrition` | fct_terminations | dim_employee, dim_org, dim_date (many_to_one) | Exits by reason, type, tenure at exit and department. No rates: the denominator is in `headcount` |
| `recruiting` | fct_application_funnel | fct_requisitions, dim_org (many_to_one); fct_hires (one_to_one) | Funnel conversion, offer acceptance, time to hire, time to fill and open requisitions. Always filtered on `fct_requisitions.employment_type`, starting at `fte` |

### Why attrition holds under the dashboard filters

In the `headcount` explore, `fct_terminations` joins on `employee_id` and
`snapshot_date = termination_date`. A leaver still counts in headcount on their last day, so each exit
lands on exactly one headcount row: the leaver's own row on the day they left. All 220 FTE exits do,
with the same department and location.

- The join is one_to_one, so nothing fans out.
- Each exit takes the date, department, location, level and tenure of its row.
- So any filter narrows the numerator and the denominator together. The exception is the day count:
  `avg_headcount` divides by the days that have FTE rows in the selection, so a slice with no FTEs on
  some days reads high and its rate low. In the current data every division, department, location,
  level and tenure band has FTEs on every day, so one filter at a time is safe; a combination may not
  be (Engineering - Data, Singapore, under a year, Q3 2026 gives 2.0 instead of 1.07). For small
  combined slices use the Data Studio views, whose grid is dense.
- Filters that pick single days (Is Month End, Is Quarter End, Is Weekend, Day Name) also drop the
  exits dated on other days. Use them for headcount only.

Two simpler designs get this wrong:

- Joining exits on department and date would repeat each exit on every headcount row in its
  department that day.
- Taking exits and headcount from separate explores leaves each half to its own filters.

Only the measures come through the join (`fields:`), with the hidden primary key. Exit reason and
type are browsed in the `attrition` explore, where grouping by them can't distort a rate. The exit
measures read their columns directly rather than through the exit dimensions, because Looker rejects
a measure whose SQL or filters use a field that the join leaves out.

### Access: rows and salary

- **Row-level filter.** Every explore has an `access_filter` on `dim_org.division_name`, driven by the
  user attribute `department`, which holds a division name. A department head sees only their own
  division, in every explore and on every tile. HR and admins get a value that matches every
  division.
- **Salary grant.** `dim_employee.salary_local` and `currency` are hidden and need the
  `can_see_salary` access grant (user attribute `can_see_salary` = "yes"). Users without it can't
  select or query them. `salary_local` is in local currency, so it's never summed or averaged across
  currencies.

### Validate

With no Looker instance to run, `make validate-lookml` checks the project offline
([scripts/validate_lookml.py](../scripts/validate_lookml.py)):

- every file parses, and every constant it uses is defined;
- each view has a table and one primary key, and its field types, timeframes and value formats are
  ones Looker knows;
- each view's table is a dbt mart, and every `${TABLE}.column` is a column listed in
  `dbt/models/marts/_marts.yml`;
- every `${...}` and Liquid field reference resolves;
- every measure has a description;
- every join has a relationship and a condition that references the joined view;
- no field an explore keeps uses, in its SQL or filters, a field that a `fields:` parameter leaves
  out;
- no median, and no raw SQL aggregate in a `type: number` measure, is computed over fanned-out rows;
- access filters and always filters use fields in their explore, and access grants and datagroups
  exist;
- every dashboard tile's fields, filters, sorts and pivots exist.

`STRICT=1` fails on warnings as well. Current output:

```
LookML in lookml:
  parse           11 files       0 errors, 0 warnings
  constants       18 uses        0 errors, 0 warnings
  views            8 views       0 errors, 0 warnings
  references     381 references  0 errors, 0 warnings
  descriptions   178 fields      0 errors, 0 warnings
  measures        36 measures    0 errors, 0 warnings
  model            2 includes    0 errors, 0 warnings
  explores         3 explores    0 errors, 0 warnings
  fanout           3 explores    0 errors, 0 warnings
  dashboards       8 tiles       0 errors, 0 warnings
User attributes referenced:
  can_see_salary: access_grant can_see_salary in model people_analytics
  department: access_filter on explore attrition; access_filter on explore headcount; access_filter on explore recruiting
LookML OK: 8 views, 3 explores, 8 dashboard tiles, 0 errors (0 warnings)
```

What it can't check: Looker's own compile of filter expressions, and how tiles render. The SQL Looker
would generate for the `headcount` and `recruiting` explores was run by hand against `analytics`. It
gives the same Q3 2026 numbers as the dashboard:

- company: average headcount 584.4, 24 leavers, 4.11%;
- Engineering: 197.3, 13 leavers, 6.59%.

Where LookML and the Data Studio report differ:

- **Average headcount on a sparse slice:** Data Studio divides FTE days by every day in the range.
  LookML's `avg_headcount` divides by the days that have FTE rows in the selection, so on a slice
  with nobody in it on some days it reads high and its rates read low. In the current data the
  company and any single division, department or location have FTEs every day, so the two agree
  there. Example, Q3 2026, Engineering - Data, Singapore, under a year (FTEs on 49 of 92 days): 1.07
  in Data Studio, 2.0 in LookML. Over 2025-10-01 to 2026-09-30, Legal, Remote, 1-3 years has 1 exit:
  173% in Data Studio, 100% in LookML.
- **Tenure band labels:** 'Under 1 year' in LookML, '< 1 year' in Data Studio. The cut-offs are the
  same.
- **Time to fill:** in LookML it's a mean only, because Looker's symmetric aggregates don't cover
  medians over the fanned-out requisition rows. Data Studio also has the median, because
  `days_to_fill` is set only on the accepted application.
- **Open requisitions:** LookML has them; the Data Studio report doesn't.
