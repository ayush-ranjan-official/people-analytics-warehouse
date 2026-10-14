-- Grain: one row per calendar day, 2023-01-01 to 2027-12-31.
-- The fiscal year runs April to March and is named after the year it ends (FY2027 = Apr 2026 - Mar 2027).

with days as (
    select date_day
    from unnest(generate_date_array(date '2023-01-01', date '2027-12-31')) as date_day
)

select
    date_day,
    date_trunc(date_day, month) as month_start,
    last_day(date_day, month) as month_end,
    cast(format_date('%Y%m', date_day) as int64) as month_key,
    extract(year from date_day) as calendar_year,
    extract(quarter from date_day) as calendar_quarter,
    format_date('%Y-Q', date_day) || cast(extract(quarter from date_day) as string) as year_quarter,
    format_date('%B', date_day) as month_name,
    format_date('%A', date_day) as day_name,
    extract(dayofweek from date_day) in (1, 7) as is_weekend,
    date_day = last_day(date_day, month) as is_month_end,
    date_day = last_day(date_day, quarter) as is_quarter_end,
    extract(year from date_add(date_day, interval 9 month)) as fiscal_year,
    extract(quarter from date_add(date_day, interval 9 month)) as fiscal_quarter
from days
