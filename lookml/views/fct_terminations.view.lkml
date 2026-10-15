# One row per FTE exit event (contractors and interns are excluded: a contract or internship ending
# isn't attrition). A rehired person can leave twice under two employee_ids, so the measures count
# exit events. termination_date is the last day employed: the person leaves headcount the next day.

view: fct_terminations {
  sql_table_name: `@{bq_project}.@{bq_dataset}.fct_terminations` ;;
  label: "Exits"

  dimension: termination_key {
    primary_key: yes
    hidden: yes
    type: number
    value_format_name: id
    sql: ${TABLE}.termination_key ;;
    description: "Surrogate key for the exit."
  }

  dimension: termination_id {
    type: string
    sql: ${TABLE}.termination_id ;;
    description: "HRIS exit record id."
  }

  dimension: employee_id {
    type: string
    sql: ${TABLE}.employee_id ;;
    description: "HRIS employee id of the employment record that ended."
  }

  dimension: employee_key {
    hidden: yes
    type: number
    value_format_name: id
    sql: ${TABLE}.employee_key ;;
    description: "The job version in effect on the exit date. Join key to dim_employee."
  }

  dimension: person_id {
    type: string
    sql: ${TABLE}.person_id ;;
    description: "The same person across rehires. Count distinct person_id to count people rather than exits."
  }

  dimension_group: termination {
    label: "Exit"
    type: time
    timeframes: [raw, date, week, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.termination_date ;;
    description: "Last day employed. The person leaves headcount the next day."
  }

  dimension: termination_type {
    label: "Exit Type"
    type: string
    sql: ${TABLE}.termination_type ;;
    description: "'voluntary' (the person chose to leave), 'involuntary' (the company ended it) or 'retirement'."
  }

  dimension: termination_type_code {
    label: "Exit Type Code"
    type: string
    sql: ${TABLE}.termination_type_code ;;
    description: "The HRIS code for the exit type: V, I or R."
  }

  dimension: reason_code {
    type: string
    sql: ${TABLE}.reason_code ;;
    description: "HRIS reason code, e.g. 'VOL_COMPENSATION'."
  }

  dimension: reason {
    label: "Exit Reason"
    type: string
    sql: ${TABLE}.reason ;;
    description: "The reason in plain English, e.g. 'Compensation'."
  }

  dimension: regretted_flag {
    label: "Regretted"
    type: yesno
    sql: ${TABLE}.regretted_flag ;;
    description: "True if the manager marked the leaver as someone they wanted to keep. Set only for voluntary exits; involuntary exits and retirements show No."
  }

  dimension_group: hire {
    type: time
    timeframes: [raw, date, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.hire_date ;;
    description: "First day of the employment record that ended."
  }

  dimension: tenure_days_at_exit {
    group_label: "Tenure at exit"
    type: number
    sql: ${TABLE}.tenure_days_at_exit ;;
    description: "Days from hire date to termination date."
  }

  dimension: tenure_years_at_exit {
    group_label: "Tenure at exit"
    type: number
    value_format_name: decimal_2
    sql: ${tenure_days_at_exit} / 365.25 ;;
    description: "Tenure at exit in years: tenure days at exit divided by 365.25."
  }

  dimension: tenure_band_at_exit {
    group_label: "Tenure at exit"
    description: "Tenure at exit in bands: under 1, 1-3, 3-5 and 5+ years."
    case: {
      when: {
        sql: ${tenure_years_at_exit} < 1 ;;
        label: "Under 1 year"
      }
      when: {
        sql: ${tenure_years_at_exit} < 3 ;;
        label: "1-3 years"
      }
      when: {
        sql: ${tenure_years_at_exit} < 5 ;;
        label: "3-5 years"
      }
      else: "5+ years"
    }
  }

  dimension: department_id {
    hidden: yes
    type: string
    sql: ${TABLE}.department_id ;;
    description: "Department or team on the exit date. Join key to dim_org."
  }

  dimension: location {
    type: string
    sql: ${TABLE}.location ;;
    description: "Work location on the exit date."
  }

  dimension: job_level {
    type: string
    sql: ${TABLE}.job_level ;;
    description: "Level on the exit date, 'L1' to 'L7'."
  }

  dimension: job_level_number {
    type: number
    sql: ${TABLE}.job_level_number ;;
    description: "Level on the exit date, as a number 1 to 7."
  }

  dimension: manager_id {
    type: string
    sql: ${TABLE}.manager_id ;;
    description: "employee_id of their manager on the exit date."
  }

  measure: leavers {
    type: count
    description: "FTE exit events (all types). The numerator of attrition rate. Example: Q3 2026 = 24."
  }

  # The next four read columns, not dimensions: the headcount explore joins this view with its
  # measures only, and Looker rejects a measure whose sql or filters use a field left out.

  measure: voluntary_leavers {
    type: count_distinct
    sql: case when ${TABLE}.termination_type = 'voluntary' then ${TABLE}.termination_key end ;;
    description: "FTE exits where the person chose to leave. Example: Q3 2026 = 20."
  }

  measure: involuntary_leavers {
    type: count_distinct
    sql: case when ${TABLE}.termination_type = 'involuntary' then ${TABLE}.termination_key end ;;
    description: "FTE exits where the company ended the employment. Retirements are counted in neither this nor voluntary leavers. Example: Q3 2026 = 3."
  }

  measure: regretted_leavers {
    type: count_distinct
    sql: case when ${TABLE}.regretted_flag then ${TABLE}.termination_key end ;;
    description: "Voluntary FTE exits the manager marked as regretted (someone they wanted to keep). Example: Q3 2026 = 10, half of the 20 voluntary leavers."
  }

  measure: avg_tenure_years_at_exit {
    label: "Average Tenure at Exit (years)"
    type: average
    value_format_name: decimal_2
    sql: ${TABLE}.tenure_days_at_exit / 365.25 ;;
    description: "Mean tenure of leavers at their exit, in years (tenure days at exit / 365.25). Example: Q3 2026 leavers = 2.03 years."
  }
}
