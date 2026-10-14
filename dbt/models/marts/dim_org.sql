-- Grain: one row per department (teams and their parent departments).
-- Names the source got wrong are corrected from the department_name_corrections seed and flagged,
-- so the fix also gets made in the HRIS rather than living here forever.

with departments as (
    select
        departments.department_id,
        departments.department_name as source_department_name,
        coalesce(corrections.corrected_name, departments.department_name) as department_name,
        corrections.source_name is not null as name_corrected_in_warehouse,
        departments.parent_department_id,
        departments.cost_center
    from {{ ref('stg_hris__departments') }} as departments
    left join {{ ref('department_name_corrections') }} as corrections
        on departments.department_name = corrections.source_name
)

select
    departments.department_id,
    departments.department_name,
    departments.source_department_name,
    departments.name_corrected_in_warehouse,
    departments.parent_department_id,
    parents.department_name as parent_department_name,
    coalesce(parents.department_name, departments.department_name) as division_name,
    departments.cost_center,
    not exists (
        select 1 from departments as children
        where children.parent_department_id = departments.department_id
    ) as is_team
from departments
left join departments as parents
    on departments.parent_department_id = parents.department_id
