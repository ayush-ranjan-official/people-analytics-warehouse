-- Grain: one row per candidate per requisition. The ATS can record a double submission as two
-- applications; only the first (lowest application_id) is kept.

with applications as (
    select
        trim(application_id) as application_id,
        trim(req_id) as req_id,
        trim(candidate_id) as candidate_id,
        trim(source) as source,
        applied_date,
        lower(trim(current_stage)) as current_stage,
        stage_updated_at,
        trim(hired_employee_id) as hired_employee_id,
        _fivetran_synced
    from {{ source('ats', 'applications') }}
    where not _fivetran_deleted
)

select
    application_id,
    req_id,
    candidate_id,
    source,
    applied_date,
    current_stage,
    stage_updated_at,
    hired_employee_id,
    _fivetran_synced
from applications
qualify row_number() over (partition by req_id, candidate_id order by application_id) = 1
