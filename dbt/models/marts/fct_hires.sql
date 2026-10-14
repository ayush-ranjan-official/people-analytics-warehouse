-- Grain: one row per hire event (an FTE employee record starting) since history_start_date.
-- Rehires are hires too: a returning person gets a new employee_id (is_rehire = true).
-- Excludes contractors and interns, and the opening population (hired before the history starts).

with hires as (
    select employee_id, hire_date
    from {{ ref('stg_hris__employees') }}
    where employment_type = 'fte'
      and hire_date >= date '{{ var("history_start_date") }}'
),

first_versions as (
    select employee_key, employee_id, person_id, change_reason, department_id, location,
           job_level, job_level_number, position_id
    from {{ ref('dim_employee') }}
    where valid_from = hire_date
)

select
    farm_fingerprint(hires.employee_id) as hire_key,
    hires.employee_id,
    first_versions.employee_key,
    first_versions.person_id,
    hires.hire_date,
    first_versions.change_reason = 'rehire' as is_rehire,
    first_versions.department_id,
    first_versions.location,
    first_versions.job_level,
    first_versions.job_level_number,
    first_versions.position_id,
    applications.req_id,
    applications.application_id,
    applications.source as application_source,
    requisitions.opened_date as req_opened_date,
    requisitions.closed_date as offer_accepted_date,
    date_diff(requisitions.closed_date, requisitions.opened_date, day) as days_to_fill,
    date_diff(requisitions.closed_date, applications.applied_date, day) as days_to_hire,
    date_diff(hires.hire_date, requisitions.closed_date, day) as days_offer_to_start
from hires
left join first_versions
    on hires.employee_id = first_versions.employee_id
left join {{ ref('stg_ats__applications') }} as applications
    on applications.hired_employee_id = hires.employee_id
left join {{ ref('stg_ats__requisitions') }} as requisitions
    on applications.req_id = requisitions.req_id
