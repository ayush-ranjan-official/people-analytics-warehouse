-- Grain: one row per employee record (a rehired person has a second record with a new employee_id).

select
    trim(employee_id) as employee_id,
    trim(first_name) as first_name,
    trim(last_name) as last_name,
    lower(trim(email)) as email,
    hire_date,
    termination_date,
    lower(trim(status)) as employee_status,
    case trim(employment_type)
        when 'FTE' then 'fte'
        when 'Contractor' then 'contractor'
        when 'Intern' then 'intern'
    end as employment_type,
    updated_at,
    _fivetran_synced
from {{ source('hris', 'employees') }}
where not _fivetran_deleted
