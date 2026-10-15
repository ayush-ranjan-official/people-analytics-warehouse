# People analytics model: headcount, attrition and recruiting over the BigQuery marts in the
# analytics dataset (synthetic data). Metric formulas follow docs/metric-definitions.md.

connection: "people_analytics_bigquery"

include: "/views/*.view.lkml"
include: "/dashboards/*.dashboard.lookml"

# The marts are rebuilt nightly, so cached results are kept until the rebuild changes them. The
# trigger reads table metadata, which BigQuery bills nothing for; max(snapshot_date) on
# fct_headcount_daily would bill about 70 MiB per check, as it is a view over seven tables.
# max_cache_age is the backstop.
datagroup: nightly_build {
  label: "Nightly build"
  description: "Changes when the nightly build rewrites the marts."
  sql_trigger: select max(last_modified_time) from `@{bq_project}.@{bq_dataset}.__TABLES__` ;;
  max_cache_age: "24 hours"
}

persist_with: nightly_build

# Salary fields require this grant. Only users whose can_see_salary attribute is "yes" (compensation
# analysts, HR business partners) could query them.
access_grant: can_see_salary {
  user_attribute: can_see_salary
  allowed_values: ["yes"]
}

# Every explore carries the same row-level filter: the "division" user attribute holds a division
# name, so a division head only sees their own division. HR and admins get a value that matches
# every division.

explore: headcount {
  view_name: fct_headcount_daily
  label: "Headcount"
  description: "FTE headcount on a day, average headcount, attrition rates and tenure, by date, department, location and level."

  # fct_headcount_daily is a BigQuery view that expands every employee over the calendar, so queries
  # must be limited to a date range: on the snapshot date, or on a calendar quarter or year. The
  # default is the current quarter and the four before it, so the first quarter is complete and only
  # the current one is partial.
  conditionally_filter: {
    filters: [fct_headcount_daily.snapshot_date: "5 quarters"]
    unless: [dim_date.year_quarter, dim_date.calendar_year, dim_date.fiscal_year]
  }

  access_filter: {
    field: dim_org.division_name
    user_attribute: division
  }

  join: dim_employee {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fct_headcount_daily.employee_key} = ${dim_employee.employee_key} ;;
  }

  join: dim_org {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fct_headcount_daily.department_id} = ${dim_org.department_id} ;;
  }

  join: dim_date {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fct_headcount_daily.snapshot_raw} = ${dim_date.day_raw} ;;
  }

  # Each exit joins to the leaver's own headcount row on their last day (a leaver still counts on
  # that day), so every FTE exit lands on exactly one headcount row. Exits and average headcount then
  # come from the same rows, and any filter on date, department, location or level narrows both.
  # attrition_rate = leavers / avg_headcount is correct for any slice that has FTEs on every day of
  # the range (see avg_headcount for the exception). Filters that pick individual days (Is Month
  # End, Is Quarter End, Is Weekend, Day Name) also drop exits dated on other days, so use them for
  # headcount only.
  # Only the measures (and the hidden primary key) are brought in; exit attributes (reason, type)
  # belong in the attrition explore.
  join: fct_terminations {
    type: left_outer
    relationship: one_to_one
    sql_on: ${fct_headcount_daily.employee_id} = ${fct_terminations.employee_id}
      and ${fct_headcount_daily.snapshot_raw} = ${fct_terminations.termination_raw} ;;
    fields: [
      fct_terminations.termination_key,
      fct_terminations.leavers,
      fct_terminations.voluntary_leavers,
      fct_terminations.involuntary_leavers,
      fct_terminations.regretted_leavers,
      fct_terminations.avg_tenure_years_at_exit
    ]
  }
}

explore: attrition {
  view_name: fct_terminations
  label: "Attrition"
  description: "FTE exits by reason, type, tenure, department and date. For attrition rates use the Headcount explore, which has the denominator."

  access_filter: {
    field: dim_org.division_name
    user_attribute: division
  }

  join: dim_employee {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fct_terminations.employee_key} = ${dim_employee.employee_key} ;;
  }

  join: dim_org {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fct_terminations.department_id} = ${dim_org.department_id} ;;
  }

  join: dim_date {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fct_terminations.termination_raw} = ${dim_date.day_raw} ;;
  }
}

explore: recruiting {
  view_name: fct_application_funnel
  label: "Recruiting"
  description: "Applications through the hiring funnel with their requisitions and hires: conversion by stage and source, offer acceptance, time to hire and time to fill."

  # The recruiting tables include contractor and intern openings. Rather than hard-coding FTE into
  # every measure, the explore always filters on the requisition's employment type and starts at
  # 'fte' (matching fct_hires and the metric definitions). Users can change the value but not drop
  # the filter, so a contractor or intern number is always a deliberate choice.
  always_filter: {
    filters: [fct_requisitions.employment_type: "fte"]
  }

  access_filter: {
    field: dim_org.division_name
    user_attribute: division
  }

  # Many applications per requisition, so requisition measures are fanned out here. Looker's
  # symmetric aggregates keep counts, sums and averages correct (fct_requisitions has a primary
  # key), which is why that view has no median. Requisitions are reached through their applications,
  # so one with no applications would not appear in this explore.
  join: fct_requisitions {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fct_application_funnel.req_id} = ${fct_requisitions.req_id} ;;
  }

  join: dim_org {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fct_application_funnel.department_id} = ${dim_org.department_id} ;;
  }

  # People who started (fct_hires is FTE-only). Each hire comes from exactly one application.
  join: fct_hires {
    type: left_outer
    relationship: one_to_one
    sql_on: ${fct_application_funnel.application_id} = ${fct_hires.application_id} ;;
  }
}
