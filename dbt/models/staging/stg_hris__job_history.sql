-- Grain: one row per effective-dated job record. The current record ends 9999-12-31.

select
    trim(job_history_id) as job_history_id,
    trim(employee_id) as employee_id,
    effective_from,
    effective_to,
    trim(position_id) as position_id,
    trim(department_id) as department_id,
    trim(manager_id) as manager_id,
    trim(location) as location,
    -- Levels arrive as text ('L4'). A bare number (4) is accepted too and given the prefix back, so a
    -- source type change doesn't break the build; the schema contract test still reports it.
    'L' || regexp_replace(trim(cast(job_level as string)), r'^L', '') as job_level,
    safe_cast(regexp_replace(trim(cast(job_level as string)), r'^L', '') as int64) as job_level_number,
    salary_local,
    upper(trim(currency)) as currency,
    lower(replace(trim(change_reason), ' ', '_')) as change_reason,
    effective_to = date '9999-12-31' as is_current_record,
    _fivetran_synced
from {{ source('hris', 'job_history') }}
where not _fivetran_deleted
