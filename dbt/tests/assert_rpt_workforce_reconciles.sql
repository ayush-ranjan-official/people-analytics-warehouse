-- rpt_workforce_daily adds up to the marts: FTE days employed, tenure, starts and exits (by type)
-- between the first and last day of fct_headcount_daily. The grid is joined with a left join, so a
-- department, location or tenure band it doesn't know would otherwise drop rows without an error.

with period as (
    select min(snapshot_date) as first_day, max(snapshot_date) as last_day
    from {{ ref('fct_headcount_daily') }}
),

reported as (
    select
        sum(headcount) as headcount,
        sum(total_tenure_days) as total_tenure_days,
        sum(hires) as hires,
        sum(exits) as exits,
        sum(voluntary_exits) as voluntary_exits,
        sum(involuntary_exits) as involuntary_exits,
        sum(retirement_exits) as retirement_exits,
        sum(regretted_exits) as regretted_exits
    from {{ ref('rpt_workforce_daily') }}
),

headcount as (
    select
        count(*) as headcount,
        sum(date_diff(headcount.snapshot_date, employees.hire_date, day)) as total_tenure_days
    from {{ ref('fct_headcount_daily') }} as headcount
    left join {{ ref('dim_employee') }} as employees
        on headcount.employee_key = employees.employee_key
    where headcount.is_fte
),

hires as (
    select count(*) as hires
    from {{ ref('fct_hires') }}, period
    where hire_date between period.first_day and period.last_day
),

exits as (
    select
        count(*) as exits,
        countif(termination_type = 'voluntary') as voluntary_exits,
        countif(termination_type = 'involuntary') as involuntary_exits,
        countif(termination_type = 'retirement') as retirement_exits,
        countif(regretted_flag) as regretted_exits
    from {{ ref('fct_terminations') }}, period
    where termination_date between period.first_day and period.last_day
),

compared as (
    select 'headcount' as measure, reported.headcount as in_reporting, headcount.headcount as in_marts
    from reported, headcount
    union all
    select 'total_tenure_days', reported.total_tenure_days, headcount.total_tenure_days from reported, headcount
    union all
    select 'hires', reported.hires, hires.hires from reported, hires
    union all
    select 'exits', reported.exits, exits.exits from reported, exits
    union all
    select 'voluntary_exits', reported.voluntary_exits, exits.voluntary_exits from reported, exits
    union all
    select 'involuntary_exits', reported.involuntary_exits, exits.involuntary_exits from reported, exits
    union all
    select 'retirement_exits', reported.retirement_exits, exits.retirement_exits from reported, exits
    union all
    select 'regretted_exits', reported.regretted_exits, exits.regretted_exits from reported, exits
)

select *
from compared
where in_reporting is distinct from in_marts
