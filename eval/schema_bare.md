# Warehouse schema

## dim_date

Table: `analytics.dim_date`

| column | type |
| --- | --- |
| date_day | DATE |
| month_start | DATE |
| month_end | DATE |
| month_key | INT64 |
| calendar_year | INT64 |
| calendar_quarter | INT64 |
| year_quarter | STRING |
| month_name | STRING |
| day_name | STRING |
| is_weekend | BOOL |
| is_month_end | BOOL |
| is_quarter_end | BOOL |
| fiscal_year | INT64 |
| fiscal_quarter | INT64 |

## dim_employee

Table: `analytics.dim_employee`

| column | type |
| --- | --- |
| employee_key | INT64 |
| job_history_id | STRING |
| employee_id | STRING |
| person_id | STRING |
| first_name | STRING |
| last_name | STRING |
| email | STRING |
| employment_type | STRING |
| hire_date | DATE |
| termination_date | DATE |
| valid_from | DATE |
| valid_to | DATE |
| is_latest_version | BOOL |
| is_current | BOOL |
| change_reason | STRING |
| position_id | STRING |
| department_id | STRING |
| manager_id | STRING |
| location | STRING |
| job_level | STRING |
| job_level_number | INT64 |
| salary_local | NUMERIC |
| currency | STRING |

## dim_org

Table: `analytics.dim_org`

| column | type |
| --- | --- |
| department_id | STRING |
| department_name | STRING |
| source_department_name | STRING |
| name_corrected_in_warehouse | BOOL |
| parent_department_id | STRING |
| parent_department_name | STRING |
| division_name | STRING |
| cost_center | STRING |
| is_team | BOOL |

## fct_application_funnel

Table: `analytics.fct_application_funnel`

| column | type |
| --- | --- |
| application_id | STRING |
| req_id | STRING |
| candidate_id | STRING |
| department_id | STRING |
| source | STRING |
| applied_date | DATE |
| current_stage | STRING |
| is_closed | BOOL |
| employment_type | STRING |
| hired_employee_id | STRING |
| applied_at | TIMESTAMP |
| screened_at | TIMESTAMP |
| interviewed_at | TIMESTAMP |
| offered_at | TIMESTAMP |
| hired_at | TIMESTAMP |
| closed_out_at | TIMESTAMP |
| reached_screen | BOOL |
| reached_interview | BOOL |
| reached_offer | BOOL |
| reached_hired | BOOL |
| days_applied_to_screen | INT64 |
| days_screen_to_interview | INT64 |
| days_interview_to_offer | INT64 |
| days_offer_to_hire | INT64 |

## fct_headcount_daily

Table: `analytics.fct_headcount_daily`

| column | type |
| --- | --- |
| snapshot_date | DATE |
| snapshot_month | INT64 |
| employee_key | INT64 |
| employee_id | STRING |
| person_id | STRING |
| department_id | STRING |
| location | STRING |
| job_level | STRING |
| job_level_number | INT64 |
| employment_type | STRING |
| is_fte | BOOL |

## fct_hires

Table: `analytics.fct_hires`

| column | type |
| --- | --- |
| hire_key | INT64 |
| employee_id | STRING |
| employee_key | INT64 |
| person_id | STRING |
| hire_date | DATE |
| is_rehire | BOOL |
| department_id | STRING |
| location | STRING |
| job_level | STRING |
| job_level_number | INT64 |
| position_id | STRING |
| req_id | STRING |
| application_id | STRING |
| application_source | STRING |
| req_opened_date | DATE |
| offer_accepted_date | DATE |
| days_to_fill | INT64 |
| days_to_hire | INT64 |
| days_offer_to_start | INT64 |

## fct_requisitions

Table: `analytics.fct_requisitions`

| column | type |
| --- | --- |
| req_id | STRING |
| position_id | STRING |
| department_id | STRING |
| hiring_manager_id | STRING |
| job_level | STRING |
| job_level_number | INT64 |
| opened_date | DATE |
| closed_date | DATE |
| requisition_status | STRING |
| employment_type | STRING |
| is_backfill | BOOL |
| days_to_fill | INT64 |
| applicant_count | INT64 |
| hired_employee_id | STRING |

## fct_terminations

Table: `analytics.fct_terminations`

| column | type |
| --- | --- |
| termination_key | INT64 |
| termination_id | STRING |
| employee_id | STRING |
| employee_key | INT64 |
| person_id | STRING |
| termination_date | DATE |
| termination_type | STRING |
| termination_type_code | STRING |
| reason_code | STRING |
| reason | STRING |
| regretted_flag | BOOL |
| hire_date | DATE |
| tenure_days_at_exit | INT64 |
| department_id | STRING |
| location | STRING |
| job_level | STRING |
| job_level_number | INT64 |
| manager_id | STRING |
