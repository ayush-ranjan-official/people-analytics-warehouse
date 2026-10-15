-- rpt_workforce_daily has every day from the start of the history to the reporting date, and every
-- department x location x tenure band on each of them. Data Studio's average headcount divides by
-- count_distinct(snapshot_date), which is the number of days in the period only if no filter can
-- remove a day.
-- Written with joins rather than scalar subqueries, so the compiled SQL also runs on its own (to see
-- the failing rows) and under --store-failures.

with reporting_date as (
    select reporting_date from {{ ref('int_reporting_date') }}
),

expected as (
    select
        date_diff(reporting_date.reporting_date, date '{{ var("history_start_date") }}', day) + 1 as days,
        (select count(*) from {{ ref('dim_org') }})
            * (select count(distinct location) from {{ ref('dim_employee') }})
            * 4 as rows_per_day
    from reporting_date
),

days as (
    select snapshot_date, count(*) as row_count
    from {{ ref('rpt_workforce_daily') }}
    group by snapshot_date
),

span as (
    select count(*) as day_count, min(snapshot_date) as first_day, max(snapshot_date) as last_day
    from days
)

select 'rows on ' || cast(days.snapshot_date as string) as problem, days.row_count as actual,
       expected.rows_per_day as expected
from days
cross join expected
where days.row_count != expected.rows_per_day

union all

select 'days', span.day_count, expected.days
from span
cross join expected
cross join reporting_date
where span.day_count != expected.days
   or span.first_day != date '{{ var("history_start_date") }}'
   or span.last_day != reporting_date.reporting_date
