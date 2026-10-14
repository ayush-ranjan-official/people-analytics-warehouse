-- Grain: one row per exit event of an FTE. Excludes contractors and interns (their contracts ending
-- are not attrition). Joined to the employee version that was in effect on the exit date.

with exits as (
    select terminations.*, employees.hire_date, employees.employment_type
    from {{ ref('stg_hris__terminations') }} as terminations
    left join {{ ref('stg_hris__employees') }} as employees
        on terminations.employee_id = employees.employee_id
    -- An exit whose employee can't be found is kept (employee_key stays null) so its test fails.
    where coalesce(employees.employment_type, 'fte') = 'fte'
)

select
    farm_fingerprint(exits.termination_id) as termination_key,
    exits.termination_id,
    exits.employee_id,
    versions.employee_key,
    versions.person_id,
    exits.termination_date,
    exits.termination_type,
    exits.termination_type_code,
    exits.reason_code,
    exits.reason,
    exits.regretted_flag,
    exits.hire_date,
    date_diff(exits.termination_date, exits.hire_date, day) as tenure_days_at_exit,
    versions.department_id,
    versions.location,
    versions.job_level,
    versions.job_level_number,
    versions.manager_id
from exits
left join {{ ref('dim_employee') }} as versions
    on exits.employee_id = versions.employee_id
   and exits.termination_date between versions.valid_from and versions.valid_to
