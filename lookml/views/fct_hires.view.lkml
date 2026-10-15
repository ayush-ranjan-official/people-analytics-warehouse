# One row per FTE hire (an FTE employment record starting) since 2023-10-01. hire_date is the first
# day at work; the offer was accepted earlier (offer_accepted_date), which is when the recruiting
# tables count a hire. A rehire counts as a hire.

view: fct_hires {
  sql_table_name: `@{bq_project}.@{bq_dataset}.fct_hires` ;;
  label: "Hires"

  dimension: hire_key {
    primary_key: yes
    hidden: yes
    type: number
    value_format_name: id
    sql: ${TABLE}.hire_key ;;
    description: "Surrogate key for the hire."
  }

  dimension: employee_id {
    type: string
    sql: ${TABLE}.employee_id ;;
    description: "HRIS employee id of the new employment record."
  }

  dimension: employee_key {
    hidden: yes
    type: number
    value_format_name: id
    sql: ${TABLE}.employee_key ;;
    description: "The hire's first job version. Join key to dim_employee."
  }

  dimension: person_id {
    type: string
    sql: ${TABLE}.person_id ;;
    description: "The same person across rehires."
  }

  dimension_group: hire {
    label: "Start"
    type: time
    timeframes: [raw, date, week, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.hire_date ;;
    description: "First day at work (not the day the offer was accepted)."
  }

  dimension: is_rehire {
    type: yesno
    sql: ${TABLE}.is_rehire ;;
    description: "True if the person had worked here before (a new employee_id, the same person_id)."
  }

  dimension: department_id {
    hidden: yes
    type: string
    sql: ${TABLE}.department_id ;;
    description: "Department or team they joined. Join key to dim_org."
  }

  dimension: location {
    type: string
    sql: ${TABLE}.location ;;
    description: "Work location they joined in."
  }

  dimension: job_level {
    type: string
    sql: ${TABLE}.job_level ;;
    description: "Level they joined at, 'L1' to 'L7'."
  }

  dimension: job_level_number {
    type: number
    sql: ${TABLE}.job_level_number ;;
    description: "Level they joined at, as a number 1 to 7."
  }

  dimension: position_id {
    type: string
    sql: ${TABLE}.position_id ;;
    description: "The seat they filled."
  }

  dimension: req_id {
    type: string
    sql: ${TABLE}.req_id ;;
    description: "The requisition they were hired through."
  }

  dimension: application_id {
    type: string
    sql: ${TABLE}.application_id ;;
    description: "Their application. Join key to fct_application_funnel."
  }

  dimension: application_source {
    type: string
    sql: ${TABLE}.application_source ;;
    description: "Where the candidate came from: Agency, Careers Site, Job Board, LinkedIn or Referral."
  }

  dimension_group: req_opened {
    label: "Requisition Opened"
    type: time
    timeframes: [raw, date, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.req_opened_date ;;
    description: "Day the requisition was opened."
  }

  dimension_group: offer_accepted {
    type: time
    timeframes: [raw, date, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.offer_accepted_date ;;
    description: "Day the offer was accepted (the requisition's closed date)."
  }

  dimension: days_to_fill {
    type: number
    sql: ${TABLE}.days_to_fill ;;
    description: "Days from the requisition opening to the offer being accepted."
  }

  dimension: days_to_hire {
    type: number
    sql: ${TABLE}.days_to_hire ;;
    description: "Days from the candidate applying to the offer being accepted."
  }

  dimension: days_offer_to_start {
    type: number
    sql: ${TABLE}.days_offer_to_start ;;
    description: "Days from the offer being accepted to the first day at work."
  }

  measure: hires {
    type: count
    description: "FTE hires counted by start date (first day at work), rehires included. Differs from offers accepted in a period because people start a few weeks after accepting. Example: 45 FTEs started in Q3 2026."
  }

  measure: rehires {
    type: count
    filters: [is_rehire: "yes"]
    description: "FTE hires of people who had worked here before."
  }

  measure: avg_days_to_fill {
    label: "Average Days to Fill"
    type: average
    value_format_name: decimal_1
    sql: ${days_to_fill} ;;
    description: "Mean days from the requisition opening to the offer being accepted, for people who started. Time to fill for a period of filled requisitions is on Requisitions."
  }

  measure: avg_days_to_hire {
    label: "Average Days to Hire"
    type: average
    value_format_name: decimal_1
    sql: ${days_to_hire} ;;
    description: "Mean days from the candidate applying to accepting the offer, for people who started. Time to hire for offers accepted in a period is on Applications."
  }
}
