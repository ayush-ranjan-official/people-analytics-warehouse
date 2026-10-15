# One row per calendar day, 2023-01-01 to 2027-12-31. The fiscal year runs April to March and is
# named after the year it ends (FY2027 = 2026-04-01 to 2027-03-31).

view: dim_date {
  sql_table_name: `@{bq_project}.@{bq_dataset}.dim_date` ;;
  label: "Calendar"

  dimension: date_day {
    primary_key: yes
    hidden: yes
    type: date
    datatype: date
    convert_tz: no
    sql: ${TABLE}.date_day ;;
    description: "The calendar day (primary key). Use the Calendar Date fields to group by day or week."
  }

  dimension_group: day {
    label: "Calendar"
    type: time
    timeframes: [raw, date, week]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.date_day ;;
    description: "The calendar day. Month, quarter and year come from the calendar and fiscal fields below."
  }

  dimension_group: month_start {
    type: time
    timeframes: [raw, date]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.month_start ;;
    description: "First day of the month."
  }

  dimension_group: month_end {
    type: time
    timeframes: [raw, date]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.month_end ;;
    description: "Last day of the month."
  }

  dimension: month_key {
    hidden: yes
    type: number
    value_format_name: id
    sql: ${TABLE}.month_key ;;
    description: "Year and month as an integer, yyyymm (e.g. 202609). Joins to fct_headcount_daily.snapshot_month."
  }

  dimension: calendar_year {
    type: number
    value_format_name: id
    sql: ${TABLE}.calendar_year ;;
    description: "Calendar year, e.g. 2026."
  }

  dimension: calendar_quarter {
    type: number
    sql: ${TABLE}.calendar_quarter ;;
    description: "Calendar quarter, 1 to 4 (Q1 = January to March)."
  }

  dimension: year_quarter {
    label: "Year and Quarter"
    type: string
    sql: ${TABLE}.year_quarter ;;
    description: "Calendar year and quarter as text, e.g. '2026-Q3'."
  }

  dimension: month_number {
    hidden: yes
    type: number
    sql: extract(month from ${TABLE}.date_day) ;;
    description: "Month of the year, 1 to 12. Sorts Month Name."
  }

  dimension: month_name {
    type: string
    order_by_field: month_number
    sql: ${TABLE}.month_name ;;
    description: "Month name in English, e.g. 'September'."
  }

  dimension: day_of_week_number {
    hidden: yes
    type: number
    sql: mod(extract(dayofweek from ${TABLE}.date_day) + 5, 7) ;;
    description: "Day of the week, 0 (Monday) to 6 (Sunday). Sorts Day Name."
  }

  dimension: day_name {
    type: string
    order_by_field: day_of_week_number
    sql: ${TABLE}.day_name ;;
    description: "Day of the week in English, e.g. 'Monday'."
  }

  dimension: is_weekend {
    type: yesno
    sql: ${TABLE}.is_weekend ;;
    description: "True on Saturdays and Sundays."
  }

  dimension: is_month_end {
    type: yesno
    sql: ${TABLE}.is_month_end ;;
    description: "True on the last day of a month. Filter on it to take headcount at each month end. Headcount only: with this filter, exits on other days drop out, so the attrition rates read near zero."
  }

  dimension: is_quarter_end {
    type: yesno
    sql: ${TABLE}.is_quarter_end ;;
    description: "True on the last day of a calendar quarter. Filter on it to take headcount at each quarter end. Headcount only: with this filter, exits on other days drop out, so the attrition rates read near zero."
  }

  dimension: fiscal_year {
    type: number
    value_format_name: id
    sql: ${TABLE}.fiscal_year ;;
    description: "Fiscal year (April to March), named after the year it ends: FY2027 = 2026-04-01 to 2027-03-31."
  }

  dimension: fiscal_quarter {
    type: number
    sql: ${TABLE}.fiscal_quarter ;;
    description: "Fiscal quarter, 1 to 4 (Q1 = April to June)."
  }
}
