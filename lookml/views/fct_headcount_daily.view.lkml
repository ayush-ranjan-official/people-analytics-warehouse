# One row per employment record per day employed, from 2023-10-01 to the reporting date (the HR
# system's latest business day, which is behind the calendar). All employment types are included;
# the headcount measures count FTEs only (is_fte). In BigQuery this is a view, so always filter on
# the snapshot date.
#
# Tenure fields and the attrition rates reference dim_employee and fct_terminations, so they only
# work in the headcount explore, which joins both.

view: fct_headcount_daily {
  sql_table_name: `@{bq_project}.@{bq_dataset}.fct_headcount_daily` ;;
  label: "Headcount"

  dimension: headcount_row_key {
    primary_key: yes
    hidden: yes
    type: string
    sql: concat(cast(${TABLE}.snapshot_date as string), '|', ${TABLE}.employee_id) ;;
    description: "snapshot_date + employee_id, the table's composite key."
  }

  dimension_group: snapshot {
    type: time
    timeframes: [raw, date, week, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.snapshot_date ;;
    description: "The day being counted. The data stops at the reporting date, not today's calendar date."
  }

  # The table's own snapshot_month column (yyyymm). The name snapshot_month belongs to the dimension
  # group above.
  dimension: snapshot_month_key {
    hidden: yes
    type: number
    value_format_name: id
    sql: ${TABLE}.snapshot_month ;;
    description: "Month of snapshot_date as yyyymm (e.g. 202609). Joins to dim_date.month_key."
  }

  dimension: employee_key {
    hidden: yes
    type: number
    value_format_name: id
    sql: ${TABLE}.employee_key ;;
    description: "The employee's job version in effect that day. Join key to dim_employee."
  }

  dimension: employee_id {
    type: string
    sql: ${TABLE}.employee_id ;;
    description: "HRIS employee id."
  }

  dimension: person_id {
    type: string
    sql: ${TABLE}.person_id ;;
    description: "The same person across rehires. Count distinct person_id to count people rather than records."
  }

  dimension: department_id {
    hidden: yes
    type: string
    sql: ${TABLE}.department_id ;;
    description: "Department or team that day. Join key to dim_org."
  }

  dimension: location {
    type: string
    sql: ${TABLE}.location ;;
    description: "Work location that day."
  }

  dimension: job_level {
    type: string
    sql: ${TABLE}.job_level ;;
    description: "Job level that day, 'L1' to 'L7'."
  }

  dimension: job_level_number {
    type: number
    sql: ${TABLE}.job_level_number ;;
    description: "Job level that day as a number, 1 to 7."
  }

  dimension: employment_type {
    type: string
    sql: ${TABLE}.employment_type ;;
    description: "'fte', 'contractor' or 'intern'."
  }

  dimension: is_fte {
    label: "Is FTE"
    type: yesno
    sql: ${TABLE}.is_fte ;;
    description: "True for full-time employees. The headcount measures count only these rows."
  }

  # Tenure on the snapshot date, from the current employment record's hire date (a rehire's tenure
  # restarts).

  dimension: tenure_years {
    group_label: "Tenure"
    type: number
    value_format_name: decimal_2
    sql: date_diff(${snapshot_raw}, ${dim_employee.hire_raw}, day) / 365.25 ;;
    description: "Years from the employment record's hire date to the snapshot date. A rehire's tenure restarts."
  }

  dimension: tenure_band {
    group_label: "Tenure"
    description: "Tenure on the snapshot date in bands: under 1, 1-3, 3-5 and 5+ years. Count headcount by band on a single day (or at each month or quarter end) for the tenure distribution."
    case: {
      when: {
        sql: ${tenure_years} < 1 ;;
        label: "Under 1 year"
      }
      when: {
        sql: ${tenure_years} < 3 ;;
        label: "1-3 years"
      }
      when: {
        sql: ${tenure_years} < 5 ;;
        label: "3-5 years"
      }
      else: "5+ years"
    }
  }

  # Headcount

  measure: headcount {
    type: count
    filters: [is_fte: "yes"]
    description: "FTEs employed on the day; a leaver still counts on their last day. Use with a single day, or group by day or by month or quarter end: over a date range it adds up person-days. For a period use Average Headcount. Example: 2026-09-30 = 595."
  }

  measure: headcount_days {
    hidden: yes
    type: count
    filters: [is_fte: "yes"]
    description: "FTE rows in the selection, i.e. FTE person-days. Numerator of average headcount."
  }

  measure: days_in_period {
    hidden: yes
    type: count_distinct
    sql: ${snapshot_raw} ;;
    filters: [is_fte: "yes"]
    description: "Distinct days with FTE rows in the selection. Denominator of average headcount."
  }

  measure: avg_headcount {
    label: "Average Headcount"
    type: number
    value_format_name: decimal_1
    sql: ${headcount_days} / nullif(${days_in_period}, 0) ;;
    description: "The mean of the daily FTE headcount over every day of the period: FTE rows divided by days in the period. The denominator of every attrition rate; don't use the headcount at the start or end of the period instead. Days are counted from the rows selected, so a group with no FTEs on some days (one level in one small team, say) reads high. Example: Q3 2026 (92 days) = 584.4."
  }

  measure: avg_tenure_years {
    label: "Average Tenure (years)"
    type: average
    value_format_name: decimal_2
    sql: case when ${is_fte} then ${tenure_years} end ;;
    description: "For FTEs employed on a date: the mean of (date - hire date), in years. Tenure runs from the current employment record's hire date, so a rehire's tenure restarts. Use with a single day; over a range it averages every FTE-day. Headcount explore only. Example: 2026-09-30 = 2.76 years."
  }

  measure: median_tenure_years {
    label: "Median Tenure (years)"
    type: median
    value_format_name: decimal_2
    sql: case when ${is_fte} then ${tenure_years} end ;;
    description: "Median tenure in years of FTEs employed on a date, from the current employment record's hire date. Use with a single day. Headcount explore only. Example: 2026-09-30 = about 2.2 years."
  }

  # Attrition rates. Exits come from fct_terminations, joined to each leaver's headcount row on their
  # last day, so both parts of the rate always cover the same people and filters. Rates are for the
  # period selected and are not annualised.

  measure: attrition_rate {
    type: number
    value_format_name: percent_2
    sql: ${fct_terminations.leavers} / nullif(${avg_headcount}, 0) ;;
    description: "FTE exits in the period divided by average FTE headcount in the period. Not annualised. Don't combine with day filters such as Is Month End, Is Quarter End, Is Weekend or Day Name: they also drop the exits dated on other days. Example: Q3 2026 = 24 / 584.4 = 4.11%; Engineering division 13 / 197.3 = 6.59%."
  }

  measure: voluntary_attrition_rate {
    type: number
    value_format_name: percent_2
    sql: ${fct_terminations.voluntary_leavers} / nullif(${avg_headcount}, 0) ;;
    description: "As attrition rate, counting only voluntary exits (the person chose to leave). Involuntary exits and retirements are excluded from the numerator, not the denominator. Example: Q3 2026 = 20 / 584.4 = 3.42%."
  }

  measure: involuntary_attrition_rate {
    type: number
    value_format_name: percent_2
    sql: ${fct_terminations.involuntary_leavers} / nullif(${avg_headcount}, 0) ;;
    description: "As attrition rate, counting only involuntary exits (the company ended the employment). Retirements are in neither the voluntary nor the involuntary rate. Example: Q3 2026 = 3 / 584.4 = 0.51%."
  }

  measure: regretted_attrition_rate {
    type: number
    value_format_name: percent_2
    sql: ${fct_terminations.regretted_leavers} / nullif(${avg_headcount}, 0) ;;
    description: "As attrition rate, counting only regretted exits: voluntary leavers the manager wanted to keep. Example: Q3 2026 = 10 / 584.4 = 1.71%."
  }
}
