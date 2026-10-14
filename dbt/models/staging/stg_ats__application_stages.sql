-- Grain: one row per stage an application entered.

select
    trim(stage_event_id) as stage_event_id,
    trim(application_id) as application_id,
    lower(trim(stage)) as stage,
    entered_at,
    _fivetran_synced
from {{ source('ats', 'application_stages') }}
where not _fivetran_deleted
