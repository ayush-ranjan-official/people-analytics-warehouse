-- Grain: one row per application (one candidate applying to one requisition), with the time each
-- stage was reached. A stage that was never reached is null.
-- hired_at is when the offer was accepted, not the first day (that's fct_hires.hire_date).
-- Includes applications to contractor and intern openings: filter employment_type = 'fte' to match
-- fct_hires.

with stages as (
    select
        application_id,
        min(case when stage = 'applied' then entered_at end) as applied_at,
        min(case when stage = 'screen' then entered_at end) as screened_at,
        min(case when stage = 'interview' then entered_at end) as interviewed_at,
        min(case when stage = 'offer' then entered_at end) as offered_at,
        min(case when stage = 'hired' then entered_at end) as hired_at,
        min(case when stage in ('rejected', 'withdrawn', 'offer_declined') then entered_at end) as closed_out_at
    from {{ ref('stg_ats__application_stages') }}
    group by application_id
)

select
    applications.application_id,
    applications.req_id,
    applications.candidate_id,
    requisitions.department_id,
    applications.source,
    applications.applied_date,
    applications.current_stage,
    applications.current_stage in ('rejected', 'withdrawn', 'offer_declined', 'hired') as is_closed,
    requisitions.employment_type,
    applications.hired_employee_id,
    stages.applied_at,
    stages.screened_at,
    stages.interviewed_at,
    stages.offered_at,
    stages.hired_at,
    stages.closed_out_at,
    stages.screened_at is not null as reached_screen,
    stages.interviewed_at is not null as reached_interview,
    stages.offered_at is not null as reached_offer,
    stages.hired_at is not null as reached_hired,
    date_diff(date(stages.screened_at), date(stages.applied_at), day) as days_applied_to_screen,
    date_diff(date(stages.interviewed_at), date(stages.screened_at), day) as days_screen_to_interview,
    date_diff(date(stages.offered_at), date(stages.interviewed_at), day) as days_interview_to_offer,
    date_diff(date(stages.hired_at), date(stages.offered_at), day) as days_offer_to_hire
from {{ ref('stg_ats__applications') }} as applications
left join stages
    on applications.application_id = stages.application_id
left join {{ ref('stg_ats__requisitions') }} as requisitions
    on applications.req_id = requisitions.req_id
