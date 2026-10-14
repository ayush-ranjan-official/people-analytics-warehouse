-- Grain: one row per exit event.

select
    trim(t.termination_id) as termination_id,
    trim(t.employee_id) as employee_id,
    t.termination_date,
    trim(t.termination_type) as termination_type_code,
    case trim(t.termination_type)
        when 'V' then 'voluntary'
        when 'I' then 'involuntary'
        when 'R' then 'retirement'
    end as termination_type,
    trim(t.reason_code) as reason_code,
    r.reason,
    t.regretted_flag,
    t._fivetran_synced
from {{ source('hris', 'terminations') }} as t
left join {{ ref('termination_reasons') }} as r
    on trim(t.reason_code) = r.reason_code
where not t._fivetran_deleted
