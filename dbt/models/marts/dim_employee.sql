-- Grain: one row per employee per job-history version (SCD Type 2 from the HRIS's own effective dates).
-- A person who left and came back has two employee_ids; person_id (first record by hire date, matched
-- on email) links them. is_current marks the version in effect today for people still employed;
-- is_latest_version marks each record's last version, including people who have left.

with employees as (
    select
        employee_id,
        first_name,
        last_name,
        email,
        hire_date,
        termination_date,
        employment_type,
        first_value(employee_id) over (partition by email order by hire_date, employee_id) as person_id
    from {{ ref('stg_hris__employees') }}
),

versions as (
    select * from {{ ref('stg_hris__job_history') }}
)

select
    farm_fingerprint(versions.job_history_id) as employee_key,
    versions.job_history_id,
    versions.employee_id,
    employees.person_id,
    employees.first_name,
    employees.last_name,
    employees.email,
    employees.employment_type,
    employees.hire_date,
    employees.termination_date,
    versions.effective_from as valid_from,
    versions.effective_to as valid_to,
    versions.effective_to = coalesce(employees.termination_date, date '9999-12-31') as is_latest_version,
    versions.effective_to = date '9999-12-31' and employees.termination_date is null as is_current,
    versions.change_reason,
    versions.position_id,
    versions.department_id,
    versions.manager_id,
    versions.location,
    versions.job_level,
    versions.job_level_number,
    versions.salary_local,
    versions.currency
from versions
inner join employees
    on versions.employee_id = employees.employee_id
