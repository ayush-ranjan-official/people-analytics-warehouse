-- FTE headcount at each month end = previous month end + FTE hires - FTE exits in between.
-- A hire counts from their first day; an exit from the day after their last day.

with month_ends as (
    select snapshot_date, count(*) as headcount
    from {{ ref('fct_headcount_daily') }}
    where is_fte
      and snapshot_date in (select date_day from {{ ref('dim_date') }} where is_month_end)
    group by snapshot_date
),

hires as (
    select date_trunc(hire_date, month) as month_start, count(*) as hires
    from {{ ref('fct_hires') }}
    group by 1
),

exits as (
    select date_trunc(date_add(termination_date, interval 1 day), month) as month_start, count(*) as exits
    from {{ ref('fct_terminations') }}
    group by 1
),

compared as (
    select
        month_ends.snapshot_date,
        month_ends.headcount,
        lag(month_ends.headcount) over (order by month_ends.snapshot_date) as previous_headcount,
        coalesce(hires.hires, 0) as hires,
        coalesce(exits.exits, 0) as exits
    from month_ends
    left join hires on date_trunc(month_ends.snapshot_date, month) = hires.month_start
    left join exits on date_trunc(month_ends.snapshot_date, month) = exits.month_start
)

select *
from compared
where previous_headcount is not null
  and headcount != previous_headcount + hires - exits
