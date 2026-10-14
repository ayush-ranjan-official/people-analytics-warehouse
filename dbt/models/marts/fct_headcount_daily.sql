{{ config(materialized='view') }}

-- Grain: one row per employee per day employed, from history_start_date to the reporting date.
-- All employment types are included; headcount metrics count FTEs only (is_fte).
-- A view rather than a table: it expands dim_employee (a few thousand rows) over the calendar at
-- query time, so it's always as current as dim_employee and costs no storage. As a table it was
-- ~32 MiB rewritten on every build, against the BigQuery sandbox's 10 GiB lifetime storage limit.

with days as (
    select date_day
    from {{ ref('dim_date') }}
    where date_day between date '{{ var("history_start_date") }}'
                       and (select reporting_date from {{ ref('int_reporting_date') }})
),

versions as (
    select employee_key, employee_id, person_id, employment_type, valid_from, valid_to, hire_date,
           coalesce(termination_date, date '9999-12-31') as last_day_employed,
           department_id, location, job_level, job_level_number
    from {{ ref('dim_employee') }}
)

select
    days.date_day as snapshot_date,
    cast(format_date('%Y%m', days.date_day) as int64) as snapshot_month,
    versions.employee_key,
    versions.employee_id,
    versions.person_id,
    versions.department_id,
    versions.location,
    versions.job_level,
    versions.job_level_number,
    versions.employment_type,
    versions.employment_type = 'fte' as is_fte
from days
inner join versions
    on days.date_day between versions.valid_from and versions.valid_to
   and days.date_day between versions.hire_date and versions.last_day_employed
