-- Grain: one row per department. Names are passed through as the source has them; dim_org corrects them.

select
    trim(department_id) as department_id,
    trim(department_name) as department_name,
    trim(parent_department_id) as parent_department_id,
    trim(cost_center) as cost_center,
    _fivetran_synced
from {{ source('hris', 'departments') }}
where not _fivetran_deleted
