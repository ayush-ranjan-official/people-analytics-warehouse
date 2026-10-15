-- Grain: one row per application, as fct_application_funnel, with names on the row and each stage as
-- a 0/1 count Data Studio can sum. All employment types are kept: filter employment_type = 'fte' to
-- match the metric definitions.
-- Each recruiting metric has its own date: applied_date for funnel cohorts, offer_accepted_date for
-- time to hire and time to fill, offer_decision_date for offer acceptance.

with applications as (
    select
        *,
        case current_stage
            when 'hired' then 'accepted'
            when 'offer_declined' then 'declined'
        end as offer_outcome
    from {{ ref('fct_application_funnel') }}
)

select
    applications.application_id,
    applications.req_id,
    applications.department_id,
    departments.department_name,
    departments.division_name,
    applications.employment_type,
    applications.source,
    applications.applied_date,
    applications.current_stage,
    applications.is_closed,
    if(applications.reached_screen, 1, 0) as screened,
    if(applications.reached_interview, 1, 0) as interviewed,
    if(applications.reached_offer, 1, 0) as offered,
    if(applications.reached_hired, 1, 0) as hired,
    if(applications.offer_outcome = 'declined', 1, 0) as offer_declined,
    applications.offer_outcome,
    if(applications.offer_outcome is not null,
       date(coalesce(applications.hired_at, applications.closed_out_at)), null) as offer_decision_date,
    date(applications.hired_at) as offer_accepted_date,
    date_diff(date(applications.hired_at), applications.applied_date, day) as days_to_hire,
    requisitions.opened_date as req_opened_date,
    -- Only on the accepted application, so averaging it counts each filled requisition once.
    if(applications.reached_hired, requisitions.days_to_fill, null) as days_to_fill
from applications
left join {{ ref('fct_requisitions') }} as requisitions
    on applications.req_id = requisitions.req_id
left join {{ ref('dim_org') }} as departments
    on applications.department_id = departments.department_id
