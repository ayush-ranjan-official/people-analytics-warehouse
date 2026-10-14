-- Grain: one row per requisition (an approved opening for one position).

select
    trim(req_id) as req_id,
    trim(position_id) as position_id,
    trim(department_id) as department_id,
    trim(hiring_manager_id) as hiring_manager_id,
    opened_date,
    closed_date,
    lower(trim(status)) as requisition_status,
    case trim(employment_type)
        when 'FTE' then 'fte'
        when 'Contractor' then 'contractor'
        when 'Intern' then 'intern'
    end as employment_type,
    _fivetran_synced
from {{ source('ats', 'requisitions') }}
where not _fivetran_deleted
