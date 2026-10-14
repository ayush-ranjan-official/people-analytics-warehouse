# Data contract: HRIS and ATS source tables

What the warehouse relies on from the source system (Postgres schema `hris`), and how changes to it
are agreed. The column names and types below are enforced by the `schema_contract` tests in
[dbt/models/staging/_sources.yml](../dbt/models/staging/_sources.yml); the allowed values are enforced
by the staging tests. If this page and the YAML ever disagree, the YAML is what runs.

## The agreement

1. **Breaking changes are announced 2 weeks ahead** to the warehouse owner: renaming or dropping a
   column, changing its type, changing the format or meaning of its values, adding a new code to a
   coded column (status, termination type, reason code, stage), or changing a primary key.
2. **Additive changes need no notice**: a new column or a new table. Fivetran brings new columns in
   by itself and the warehouse ignores them until someone asks for them.
3. A rename runs as a **transition**: the new column is added and filled, both are kept for at least
   one full sync cycle, then the old one is dropped. The warehouse reads both during the window.
4. **Corrections happen in the source.** If the warehouse finds bad data, the fix goes into the HR
   system, not into a warehouse patch ([data-quality.md](data-quality.md) shows how that went).
5. Rows are deleted in the source, not flagged: Fivetran turns deletes into `_fivetran_deleted = true`
   and staging drops them.

## How it's checked

| Check | Where | When it fires | Severity |
| --- | --- | --- | --- |
| Contract column missing or a different type | `schema_contract` (breaking), every source table | rename, drop, type change | error: `dbt build` skips everything downstream |
| Column not in the contract | `schema_contract` (additive), every source table | new column | warn |
| Value outside the allowed list | `accepted_values` on staging models | new code, changed format | error |
| `job_level` not in the `L1`..`L7` form | `expression_is_true` on the source table | format change Fivetran can't see as a type change | warn |
| Primary key unique and not null | `unique` + `not_null` on each staging model | duplicates, missing keys | error |
| Freshness on `_fivetran_synced` | `dbt source freshness` | sync stalled | warn / error per table |

Every contract test prints what changed, for example
`hris_job_history.job_level: contract says STRING, table has INT64`.

## Tables

Types are BigQuery types as landed. Every table also carries `_fivetran_deleted` (BOOL),
`_fivetran_synced` (TIMESTAMP) and `_loaded_at` (TIMESTAMP, added by the BigQuery load).

### hris_employees: one row per employment record

| Column | Type | Rules |
| --- | --- | --- |
| employee_id | STRING | primary key; a rehire gets a new id |
| first_name, last_name | STRING | |
| email | STRING | not null, valid address; the same person keeps their email across rehires |
| hire_date | DATE | |
| termination_date | DATE | null while employed |
| status | STRING | `Active`, `Terminated` |
| employment_type | STRING | `FTE`, `Contractor`, `Intern` |
| updated_at | TIMESTAMP | set on every change |

### hris_job_history: one row per change to a person's job

| Column | Type | Rules |
| --- | --- | --- |
| job_history_id | STRING | primary key |
| employee_id | STRING | references `hris_employees` |
| effective_from, effective_to | DATE | `effective_to >= effective_from`; the current record ends `9999-12-31`; one person's records don't overlap |
| position_id, department_id | STRING | reference `hris_positions`, `hris_departments` |
| manager_id | STRING | null only at the top of the org |
| location | STRING | `Bengaluru`, `London`, `New York`, `Remote`, `Singapore` |
| job_level | STRING | `L1` to `L7` |
| salary_local | NUMERIC | annual, in `currency`; between 1 and 100,000,000 |
| currency | STRING | ISO code: `INR`, `GBP`, `SGD`, `USD` |
| change_reason | STRING | `Hire`, `Rehire`, `Promotion`, `Transfer`, `Manager Change`, `Location Change`, `Merit Increase`, `Salary Adjustment` |

### hris_positions: one row per seat

| Column | Type | Rules |
| --- | --- | --- |
| position_id | STRING | primary key |
| job_title | STRING | |
| department_id | STRING | references `hris_departments` |
| job_level | STRING | `L1` to `L7`; a promotion regrades the seat in place |
| is_filled | BOOL | |
| created_at | TIMESTAMP | |
| closed_date | DATE | null while the seat exists |

### hris_departments: one row per department or team

| Column | Type | Rules |
| --- | --- | --- |
| department_id | STRING | primary key |
| department_name | STRING | one of the 11 names listed in `stg_hris__departments`; teams are named `<Department> - <Team>` |
| parent_department_id | STRING | null for a top-level department; at most two levels |
| cost_center | STRING | |

### hris_terminations: one row per exit

| Column | Type | Rules |
| --- | --- | --- |
| termination_id | STRING | primary key |
| employee_id | STRING | must exist in `hris_employees` |
| termination_date | DATE | |
| termination_type | STRING | `V` voluntary, `I` involuntary, `R` retirement |
| reason_code | STRING | one of the codes in the `termination_reasons` seed; a new code needs the seed updated first |
| regretted_flag | BOOL | set for voluntary exits only, null otherwise |

### ats_requisitions: one row per approved opening

| Column | Type | Rules |
| --- | --- | --- |
| req_id | STRING | primary key |
| position_id, department_id | STRING | reference the HRIS tables |
| hiring_manager_id | STRING | an employee_id |
| opened_date | DATE | |
| closed_date | DATE | null while open |
| status | STRING | `Open`, `Filled`, `Cancelled` |
| employment_type | STRING | `FTE`, `Contractor`, `Intern`; added after the first load (an additive change) |

### ats_applications: one row per candidate per requisition

| Column | Type | Rules |
| --- | --- | --- |
| application_id | STRING | primary key |
| req_id | STRING | references `ats_requisitions`; (req_id, candidate_id) is unique |
| candidate_id | STRING | |
| source | STRING | `Agency`, `Careers Site`, `Job Board`, `LinkedIn`, `Referral` |
| applied_date | DATE | |
| current_stage | STRING | `applied`, `screen`, `interview`, `offer`, `hired`, `rejected`, `withdrawn`, `offer_declined` |
| stage_updated_at | TIMESTAMP | |
| hired_employee_id | STRING | set once a hired candidate starts and has an HRIS record; null before the start date |

### ats_application_stages: one row per stage an application entered

| Column | Type | Rules |
| --- | --- | --- |
| stage_event_id | STRING | primary key |
| application_id | STRING | references `ats_applications` |
| stage | STRING | same values as `current_stage` |
| entered_at | TIMESTAMP | stages of one application are in order |

## Changing the contract

Update `_sources.yml` (the breaking and additive tests share one list per table), the staging model,
any mart and LookML view that exposes the column, and this page, in the same pull request. The
[schema drift runbook](runbook-schema-drift.md) walks through each kind of change.
