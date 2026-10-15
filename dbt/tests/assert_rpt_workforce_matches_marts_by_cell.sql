-- rpt_workforce_daily matches the marts cell by cell (day, department, location, tenure band), not only
-- in total. Each exit and start is placed on the person's own headcount row that day, so this also
-- checks that any filter narrows exits and average headcount together.

with own_rows as (
    select
        headcount.snapshot_date,
        headcount.employee_id,
        headcount.department_id,
        headcount.location,
        date_diff(headcount.snapshot_date, employees.hire_date, day) as tenure_days
    from {{ ref('fct_headcount_daily') }} as headcount
    inner join {{ ref('dim_employee') }} as employees
        on headcount.employee_key = employees.employee_key
    where headcount.is_fte
),

expected as (
    select
        own_rows.snapshot_date,
        own_rows.department_id,
        own_rows.location,
        case
            when own_rows.tenure_days < 365.25 then '< 1 year'
            when own_rows.tenure_days < 3 * 365.25 then '1-3 years'
            when own_rows.tenure_days < 5 * 365.25 then '3-5 years'
            else '5+ years'
        end as tenure_band,
        count(*) as headcount,
        sum(own_rows.tenure_days) as total_tenure_days,
        count(hires.employee_id) as hires,
        count(exits.employee_id) as exits,
        countif(exits.termination_type = 'voluntary') as voluntary_exits,
        countif(exits.termination_type = 'involuntary') as involuntary_exits,
        countif(exits.termination_type = 'retirement') as retirement_exits,
        countif(exits.regretted_flag) as regretted_exits
    from own_rows
    left join {{ ref('fct_hires') }} as hires
        on own_rows.employee_id = hires.employee_id and own_rows.snapshot_date = hires.hire_date
    left join {{ ref('fct_terminations') }} as exits
        on own_rows.employee_id = exits.employee_id and own_rows.snapshot_date = exits.termination_date
    group by 1, 2, 3, 4
),

reported as (
    select
        snapshot_date, department_id, location, tenure_band, headcount, total_tenure_days, hires, exits,
        voluntary_exits, involuntary_exits, retirement_exits, regretted_exits
    from {{ ref('rpt_workforce_daily') }}
    where headcount > 0 or hires > 0 or exits > 0
)

select
    snapshot_date,
    department_id,
    location,
    tenure_band,
    reported.headcount as headcount_in_view,
    expected.headcount as headcount_in_marts,
    reported.exits as exits_in_view,
    expected.exits as exits_on_own_row
from reported
full outer join expected
    using (snapshot_date, department_id, location, tenure_band)
where to_json_string(struct(
          reported.headcount, reported.total_tenure_days, reported.hires, reported.exits,
          reported.voluntary_exits, reported.involuntary_exits, reported.retirement_exits,
          reported.regretted_exits))
   != to_json_string(struct(
          expected.headcount, expected.total_tenure_days, expected.hires, expected.exits,
          expected.voluntary_exits, expected.involuntary_exits, expected.retirement_exits,
          expected.regretted_exits))
