-- Grain: one row per position (seat). A promotion regrades the seat in place.

select
    trim(position_id) as position_id,
    trim(job_title) as job_title,
    trim(department_id) as department_id,
    trim(job_level) as job_level,
    safe_cast(substr(trim(job_level), 2) as int64) as job_level_number,
    is_filled,
    created_at,
    closed_date,
    not is_filled and closed_date is null as is_open_vacancy,
    _fivetran_synced
from {{ source('hris', 'positions') }}
where not _fivetran_deleted
