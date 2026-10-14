-- Source system for the synthetic HRIS + ATS (schema `hris`).
--
-- Every table has a primary key, because logical replication needs one to replicate updates
-- and deletes. There are deliberately no foreign keys or check constraints: like many real
-- source systems, this one accepts bad rows, and the warehouse tests have to catch them.
--
-- Safe to run repeatedly. Tables are never dropped: a dropped table silently leaves the
-- replication publication and loses its grants.

create schema if not exists hris;

create table if not exists hris.hris_departments (
    department_id        text primary key,
    department_name      text,
    parent_department_id text,
    cost_center          text
);

create table if not exists hris.hris_positions (
    position_id   text primary key,
    job_title     text,
    department_id text,
    job_level     text,
    is_filled     boolean,
    created_at    timestamptz
);

create table if not exists hris.hris_employees (
    employee_id      text primary key,
    first_name       text,
    last_name        text,
    email            text,
    hire_date        date,
    termination_date date,
    status           text,
    employment_type  text,
    updated_at       timestamptz
);

create table if not exists hris.hris_job_history (
    job_history_id text primary key,
    employee_id    text,
    effective_from date,
    effective_to   date,
    position_id    text,
    department_id  text,
    manager_id     text,
    location       text,
    job_level      text,
    salary_local   numeric(14, 2),
    currency       text,
    change_reason  text
);

create table if not exists hris.hris_terminations (
    termination_id   text primary key,
    employee_id      text,
    termination_date date,
    termination_type text,
    reason_code      text,
    regretted_flag   boolean
);

create table if not exists hris.ats_requisitions (
    req_id            text primary key,
    position_id       text,
    department_id     text,
    hiring_manager_id text,
    opened_date       date,
    closed_date       date,
    status            text
);

create table if not exists hris.ats_applications (
    application_id    text primary key,
    req_id            text,
    candidate_id      text,
    source            text,
    applied_date      date,
    current_stage     text,
    stage_updated_at  timestamptz,
    hired_employee_id text
);

create table if not exists hris.ats_application_stages (
    stage_event_id text primary key,
    application_id text,
    stage          text,
    entered_at     timestamptz
);

-- Added after the first load: when a seat is eliminated (vacated and not backfilled).
alter table hris.hris_positions add column if not exists closed_date date;

-- Added after the first load: whether an opening is for an FTE, a contractor or an intern. Existing
-- rows are filled with `make backfill-source COLUMN=ats_requisitions.employment_type`.
alter table hris.ats_requisitions add column if not exists employment_type text;

-- Simulator bookkeeping (the business date reached by --simulate-day). Kept out of `hris`
-- so it's never replicated.
create schema if not exists sim;

create table if not exists sim.state (
    key        text primary key,
    value      text not null,
    updated_at timestamptz not null default now()
);
