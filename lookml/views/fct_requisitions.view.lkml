# One row per requisition (an approved opening for one seat). Contractor and intern openings are
# included: filter employment_type = 'fte' to match fct_hires. A filled requisition's closed_date is
# when the offer was accepted, not the start date.
#
# In the recruiting explore this view is fanned out (many applications per requisition). Looker's
# symmetric aggregates keep counts and averages right because req_id is the primary key, but they
# don't cover medians, so time to fill here is a mean only.

view: fct_requisitions {
  sql_table_name: `@{bq_project}.@{bq_dataset}.fct_requisitions` ;;
  label: "Requisitions"

  dimension: req_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.req_id ;;
    description: "ATS requisition id."
  }

  dimension: position_id {
    type: string
    sql: ${TABLE}.position_id ;;
    description: "The seat being hired for."
  }

  dimension: department_id {
    hidden: yes
    type: string
    sql: ${TABLE}.department_id ;;
    description: "Hiring department or team. Join key to dim_org."
  }

  dimension: hiring_manager_id {
    type: string
    sql: ${TABLE}.hiring_manager_id ;;
    description: "employee_id of the hiring manager."
  }

  dimension: job_level {
    type: string
    sql: ${TABLE}.job_level ;;
    description: "Level being hired for, 'L1' to 'L7'. For a filled requisition, the hire's starting level; otherwise the seat's current grade."
  }

  dimension: job_level_number {
    type: number
    sql: ${TABLE}.job_level_number ;;
    description: "The same level as a number, 1 to 7."
  }

  dimension_group: opened {
    type: time
    timeframes: [raw, date, week, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.opened_date ;;
    description: "Day the requisition was opened."
  }

  dimension_group: closed {
    type: time
    timeframes: [raw, date, week, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.closed_date ;;
    description: "Day it was filled (offer accepted) or cancelled. Null while open. Filter on it for time to fill in a period."
  }

  dimension: requisition_status {
    type: string
    sql: ${TABLE}.requisition_status ;;
    description: "'open', 'filled' or 'cancelled'."
  }

  dimension: employment_type {
    type: string
    sql: ${TABLE}.employment_type ;;
    description: "What the opening is for: 'fte', 'contractor' or 'intern'. The recruiting explore always filters on it, starting at 'fte'."
  }

  dimension: is_backfill {
    type: yesno
    sql: ${TABLE}.is_backfill ;;
    description: "True if the seat existed before the requisition opened (replacing someone who left or moved); false for a new seat."
  }

  dimension: days_to_fill {
    type: number
    sql: ${TABLE}.days_to_fill ;;
    description: "Days from opening to the offer being accepted. Null unless filled."
  }

  dimension: applicant_count {
    type: number
    sql: ${TABLE}.applicant_count ;;
    description: "Number of applications received (duplicates removed)."
  }

  dimension: hired_employee_id {
    type: string
    sql: ${TABLE}.hired_employee_id ;;
    description: "employee_id of the person hired. Null until they start, even if the offer was accepted."
  }

  measure: requisitions {
    type: count
    description: "Requisitions (approved openings for one seat each), whatever their status."
  }

  measure: filled_requisitions {
    type: count
    filters: [requisition_status: "filled"]
    description: "Requisitions filled, i.e. an offer was accepted. Filter Closed Date to count those filled in a period."
  }

  measure: open_requisitions {
    type: count
    filters: [requisition_status: "open"]
    description: "Requisitions still open (neither filled nor cancelled) as of the reporting date."
  }

  measure: avg_days_to_fill {
    label: "Average Days to Fill"
    type: average
    value_format_name: decimal_1
    sql: ${days_to_fill} ;;
    description: "Time to fill: mean days from a requisition opening to its offer being accepted, for filled requisitions (filter Closed Date to the period). A few hard-to-fill roles pull the mean up; the median is in the metric definitions. Example: Q3 2026, 40 FTE requisitions = 52.6 days (median 47)."
  }
}
