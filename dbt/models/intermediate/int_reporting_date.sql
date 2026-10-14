-- Grain: one row. The latest date the source data covers: the HR system's "today".
-- Daily facts run up to this date rather than the calendar date, so a stale sync never
-- counts people as employed on days the data doesn't cover yet.
-- Rows dated after the day they were synced (a change keyed in advance) don't move it, and an
-- empty source counts as the start of the history rather than making the whole date NULL.

{%- set start = "date '" ~ var('history_start_date') ~ "'" %}

select
    least(
        current_date(),
        greatest(
            coalesce((select max(effective_from) from {{ ref('stg_hris__job_history') }}
                      where effective_from <= date(_fivetran_synced)), {{ start }}),
            coalesce((select max(hire_date) from {{ ref('stg_hris__employees') }}
                      where hire_date <= date(_fivetran_synced)), {{ start }}),
            coalesce((select max(termination_date) from {{ ref('stg_hris__terminations') }}
                      where termination_date <= date(_fivetran_synced)), {{ start }}),
            coalesce((select max(date(entered_at)) from {{ ref('stg_ats__application_stages') }}
                      where date(entered_at) <= date(_fivetran_synced)), {{ start }})
        )
    ) as reporting_date
