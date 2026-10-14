-- Grain: one row per requisition (an approved opening for one position).
-- Includes contractor and intern openings: filter employment_type = 'fte' to match fct_hires.
-- job_level is the level the hire came in at; unfilled requisitions show the seat's current grade
-- (positions are regraded in place when their holder is promoted).

with applicants as (
    select req_id, count(*) as applicant_count
    from {{ ref('stg_ats__applications') }}
    group by req_id
),

hires as (
    select req_id, hired_employee_id
    from {{ ref('stg_ats__applications') }}
    where current_stage = 'hired'
),

hire_versions as (
    select employee_id, job_level, job_level_number
    from {{ ref('dim_employee') }}
    where valid_from = hire_date
)

select
    requisitions.req_id,
    requisitions.position_id,
    requisitions.department_id,
    requisitions.hiring_manager_id,
    coalesce(hire_versions.job_level, positions.job_level) as job_level,
    coalesce(hire_versions.job_level_number, positions.job_level_number) as job_level_number,
    requisitions.opened_date,
    requisitions.closed_date,
    requisitions.requisition_status,
    requisitions.employment_type,
    positions.created_at < timestamp(requisitions.opened_date) as is_backfill,
    case when requisitions.requisition_status = 'filled'
         then date_diff(requisitions.closed_date, requisitions.opened_date, day) end as days_to_fill,
    coalesce(applicants.applicant_count, 0) as applicant_count,
    hires.hired_employee_id
from {{ ref('stg_ats__requisitions') }} as requisitions
left join {{ ref('stg_hris__positions') }} as positions
    on requisitions.position_id = positions.position_id
left join applicants
    on requisitions.req_id = applicants.req_id
left join hires
    on requisitions.req_id = hires.req_id
left join hire_versions
    on hires.hired_employee_id = hire_versions.employee_id
