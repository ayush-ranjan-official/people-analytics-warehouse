-- Grain: one row per FTE exit, as fct_terminations, with the names, periods and tenure band Data Studio
-- groups by already on the row. For breakdowns by reason, type and tenure; rates need average
-- headcount, which comes from rpt_workforce_daily.

with exits as (
    select
        *,
        -- Same bands as rpt_workforce_daily (assert_rpt_exits_match_workforce_daily checks they agree).
        case
            when tenure_days_at_exit < 365.25 then 1
            when tenure_days_at_exit < 3 * 365.25 then 2
            when tenure_days_at_exit < 5 * 365.25 then 3
            else 4
        end as tenure_band_order
    from {{ ref('fct_terminations') }}
)

select
    exits.termination_key,
    exits.termination_id,
    exits.employee_id,
    exits.person_id,
    exits.termination_date,
    dates.month_start,
    dates.year_quarter,
    dates.calendar_year,
    dates.calendar_quarter,
    dates.fiscal_year,
    dates.fiscal_quarter,
    exits.department_id,
    departments.department_name,
    departments.division_name,
    exits.location,
    exits.job_level,
    exits.termination_type,
    exits.reason_code,
    exits.reason,
    exits.regretted_flag,
    exits.tenure_days_at_exit,
    exits.tenure_days_at_exit / 365.25 as tenure_years_at_exit,
    ['< 1 year', '1-3 years', '3-5 years', '5+ years'][ordinal(exits.tenure_band_order)] as tenure_band,
    exits.tenure_band_order
from exits
left join {{ ref('dim_org') }} as departments
    on exits.department_id = departments.department_id
left join {{ ref('dim_date') }} as dates
    on exits.termination_date = dates.date_day
