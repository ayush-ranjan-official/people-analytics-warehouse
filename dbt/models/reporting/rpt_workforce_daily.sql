-- Grain: one row per day x department x location x tenure band, from history_start_date to the
-- reporting date, FTEs only. Built for Data Studio, which can only aggregate the fields it's given:
-- every measure is a count, so any mix of filters (department, division, location, tenure band, dates)
-- adds up to the right answer.
-- The grid is dense (every combination on every day, zero-filled), so count_distinct(snapshot_date) is
-- the number of days in the selected period whatever the filters, and
--   average headcount = sum(headcount) / count_distinct(snapshot_date)
--   attrition rate    = sum(exits) / average headcount
-- A view: about 240k rows worked out from the marts at query time, so it costs no storage.

with reporting_date as (
    select reporting_date from {{ ref('int_reporting_date') }}
),

days as (
    select
        dates.date_day as snapshot_date,
        dates.month_start,
        dates.year_quarter,
        dates.calendar_year,
        dates.calendar_quarter,
        dates.fiscal_year,
        dates.fiscal_quarter,
        dates.is_month_end,
        dates.date_day = reporting_date.reporting_date as is_reporting_date
    from {{ ref('dim_date') }} as dates
    cross join reporting_date
    where dates.date_day between date '{{ var("history_start_date") }}' and reporting_date.reporting_date
),

locations as (
    select distinct location from {{ ref('dim_employee') }}
),

-- Tenure is measured from the employment record's hire_date, so a rehire starts again at zero.
-- rpt_exits uses the same bands (assert_rpt_exits_match_workforce_daily checks they agree).
tenure_bands as (
    select tenure_band, tenure_band_order + 1 as tenure_band_order
    from unnest(['< 1 year', '1-3 years', '3-5 years', '5+ years']) as tenure_band
        with offset as tenure_band_order
),

-- One row per FTE day employed, exit and start, with tenure in days on the day it happened. A leaver's
-- last day counts in both headcount and exits, at the same tenure; a start is at tenure zero.
events as (
    select
        headcount.snapshot_date as event_date,
        headcount.department_id,
        headcount.location,
        date_diff(headcount.snapshot_date, employees.hire_date, day) as tenure_days,
        1 as headcount,
        0 as hires,
        0 as exits,
        cast(null as string) as termination_type,
        cast(null as bool) as regretted_flag
    from {{ ref('fct_headcount_daily') }} as headcount
    inner join {{ ref('dim_employee') }} as employees
        on headcount.employee_key = employees.employee_key
    where headcount.is_fte

    union all

    select termination_date, department_id, location, tenure_days_at_exit, 0, 0, 1, termination_type, regretted_flag
    from {{ ref('fct_terminations') }}

    union all

    select hire_date, department_id, location, 0, 0, 1, 0, null, null
    from {{ ref('fct_hires') }}
),

counts as (
    select
        event_date as snapshot_date,
        department_id,
        location,
        case
            when tenure_days < 365.25 then 1
            when tenure_days < 3 * 365.25 then 2
            when tenure_days < 5 * 365.25 then 3
            else 4
        end as tenure_band_order,
        sum(headcount) as headcount,
        sum(headcount * tenure_days) as total_tenure_days,
        sum(hires) as hires,
        sum(exits) as exits,
        countif(termination_type = 'voluntary') as voluntary_exits,
        countif(termination_type = 'involuntary') as involuntary_exits,
        countif(termination_type = 'retirement') as retirement_exits,
        countif(regretted_flag) as regretted_exits
    from events
    group by 1, 2, 3, 4
)

select
    days.snapshot_date,
    days.month_start,
    days.year_quarter,
    days.calendar_year,
    days.calendar_quarter,
    days.fiscal_year,
    days.fiscal_quarter,
    days.is_month_end,
    days.is_reporting_date,
    departments.department_id,
    departments.department_name,
    departments.division_name,
    locations.location,
    tenure_bands.tenure_band,
    tenure_bands.tenure_band_order,
    coalesce(counts.headcount, 0) as headcount,
    coalesce(counts.total_tenure_days, 0) as total_tenure_days,
    coalesce(counts.hires, 0) as hires,
    coalesce(counts.exits, 0) as exits,
    coalesce(counts.voluntary_exits, 0) as voluntary_exits,
    coalesce(counts.involuntary_exits, 0) as involuntary_exits,
    coalesce(counts.retirement_exits, 0) as retirement_exits,
    coalesce(counts.regretted_exits, 0) as regretted_exits
from days
cross join {{ ref('dim_org') }} as departments
cross join locations
cross join tenure_bands
left join counts
    on days.snapshot_date = counts.snapshot_date
   and departments.department_id = counts.department_id
   and locations.location = counts.location
   and tenure_bands.tenure_band_order = counts.tenure_band_order
