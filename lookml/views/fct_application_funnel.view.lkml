# One row per application (one candidate applying to one requisition), with the time each stage was
# reached. hired_at and reached_hired mean the offer was accepted, not that the person has started
# (fct_hires.hire_date is the start). For conversion rates use applications old enough to have
# finished: recent ones are still in progress and make conversion look low.
#
# Contractor and intern openings are included. The recruiting explore filters them through
# fct_requisitions.employment_type, so this view's copy of the column is hidden to leave one filter.

view: fct_application_funnel {
  sql_table_name: `@{bq_project}.@{bq_dataset}.fct_application_funnel` ;;
  label: "Applications"

  dimension: application_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.application_id ;;
    description: "ATS application id."
  }

  dimension: req_id {
    type: string
    sql: ${TABLE}.req_id ;;
    description: "The requisition applied to."
  }

  dimension: candidate_id {
    type: string
    sql: ${TABLE}.candidate_id ;;
    description: "ATS candidate id. The same candidate can apply to several requisitions."
  }

  dimension: department_id {
    hidden: yes
    type: string
    sql: ${TABLE}.department_id ;;
    description: "Hiring department or team. Join key to dim_org."
  }

  dimension: source {
    type: string
    sql: ${TABLE}.source ;;
    description: "Where the candidate came from: Agency, Careers Site, Job Board, LinkedIn or Referral."
  }

  dimension_group: applied {
    type: time
    timeframes: [raw, date, week, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.applied_date ;;
    description: "Day the application was submitted. Filter on it to pick a cohort for funnel conversion."
  }

  dimension: current_stage {
    type: string
    sql: ${TABLE}.current_stage ;;
    description: "Latest stage reached. In progress: 'applied', 'screen', 'interview' or 'offer'; finished: 'hired', 'rejected', 'withdrawn' or 'offer_declined'."
  }

  dimension: is_closed {
    type: yesno
    sql: ${TABLE}.is_closed ;;
    description: "True once the application has finished (hired, rejected, withdrawn or offer declined)."
  }

  dimension: employment_type {
    hidden: yes
    type: string
    sql: ${TABLE}.employment_type ;;
    description: "What the requisition is for: 'fte', 'contractor' or 'intern'. Same value as the requisition's employment type, which is the one to filter on."
  }

  dimension: hired_employee_id {
    type: string
    sql: ${TABLE}.hired_employee_id ;;
    description: "employee_id of the hire, once they have started. Null otherwise."
  }

  # When each stage was reached (UTC; convert_tz is off so dates match date(<timestamp>) in SQL).

  dimension_group: applied_at {
    label: "Submitted"
    group_label: "Stage reached"
    type: time
    timeframes: [raw, time]
    convert_tz: no
    sql: ${TABLE}.applied_at ;;
    description: "When the application was submitted (UTC)."
  }

  dimension_group: screened_at {
    label: "Screened"
    group_label: "Stage reached"
    type: time
    timeframes: [raw, time, date, week, month, quarter, year]
    convert_tz: no
    sql: ${TABLE}.screened_at ;;
    description: "When it reached the screen stage. Null if it never did."
  }

  dimension_group: interviewed_at {
    label: "Interviewed"
    group_label: "Stage reached"
    type: time
    timeframes: [raw, time, date, week, month, quarter, year]
    convert_tz: no
    sql: ${TABLE}.interviewed_at ;;
    description: "When it reached the interview stage. Null if it never did."
  }

  dimension_group: offered_at {
    label: "Offered"
    group_label: "Stage reached"
    type: time
    timeframes: [raw, time, date, week, month, quarter, year]
    convert_tz: no
    sql: ${TABLE}.offered_at ;;
    description: "When an offer was made. Null if none was."
  }

  dimension_group: hired_at {
    label: "Offer Accepted"
    group_label: "Stage reached"
    type: time
    timeframes: [raw, time, date, week, month, quarter, year]
    convert_tz: no
    sql: ${TABLE}.hired_at ;;
    description: "When the offer was accepted (not the start date). Null if it wasn't. Filter on it for time to hire in a period."
  }

  dimension_group: closed_out_at {
    label: "Closed Out"
    group_label: "Stage reached"
    type: time
    timeframes: [raw, time, date, week, month, quarter, year]
    convert_tz: no
    sql: ${TABLE}.closed_out_at ;;
    description: "When it was rejected, withdrawn or the offer declined. Null if hired or still open."
  }

  dimension_group: offer_answered {
    group_label: "Stage reached"
    type: time
    timeframes: [raw, date, week, month, quarter, year]
    convert_tz: no
    sql: case when ${TABLE}.current_stage in ('hired', 'offer_declined')
              then coalesce(${TABLE}.hired_at, ${TABLE}.closed_out_at) end ;;
    description: "When the candidate accepted or declined the offer. Null if there was no offer or it is still waiting for an answer. Filter on it for offer acceptance in a period."
  }

  dimension: reached_screen {
    group_label: "Stage reached"
    type: yesno
    sql: ${TABLE}.reached_screen ;;
    description: "True if it reached the screen stage."
  }

  dimension: reached_interview {
    group_label: "Stage reached"
    type: yesno
    sql: ${TABLE}.reached_interview ;;
    description: "True if it reached the interview stage."
  }

  dimension: reached_offer {
    group_label: "Stage reached"
    type: yesno
    sql: ${TABLE}.reached_offer ;;
    description: "True if an offer was made."
  }

  dimension: reached_hired {
    group_label: "Stage reached"
    type: yesno
    sql: ${TABLE}.reached_hired ;;
    description: "True if the offer was accepted."
  }

  # Days between stages

  dimension: days_applied_to_screen {
    group_label: "Days between stages"
    type: number
    sql: ${TABLE}.days_applied_to_screen ;;
    description: "Days from applying to the screen. Null if not screened."
  }

  dimension: days_screen_to_interview {
    group_label: "Days between stages"
    type: number
    sql: ${TABLE}.days_screen_to_interview ;;
    description: "Days from the screen to the interview. Null if not interviewed."
  }

  dimension: days_interview_to_offer {
    group_label: "Days between stages"
    type: number
    sql: ${TABLE}.days_interview_to_offer ;;
    description: "Days from the interview to the offer. Null if no offer."
  }

  dimension: days_offer_to_hire {
    group_label: "Days between stages"
    type: number
    sql: ${TABLE}.days_offer_to_hire ;;
    description: "Days from the offer to its acceptance. Null if not accepted."
  }

  dimension: days_to_hire {
    group_label: "Days between stages"
    type: number
    sql: date_diff(date(${TABLE}.hired_at), ${TABLE}.applied_date, day) ;;
    description: "Time to hire: days from applying to accepting the offer. Null unless the offer was accepted."
  }

  # Funnel counts

  measure: applications {
    type: count
    description: "Applications (duplicates removed). Example: FTE applications submitted in 2025 = 11,252."
  }

  measure: screened {
    type: count
    filters: [reached_screen: "yes"]
    description: "Applications that reached the screen stage. Example: 2025 FTE cohort = 2,833."
  }

  measure: interviewed {
    type: count
    filters: [reached_interview: "yes"]
    description: "Applications that reached the interview stage. Example: 2025 FTE cohort = 931."
  }

  measure: offered {
    type: count
    filters: [reached_offer: "yes"]
    description: "Applications that got an offer. Example: 2025 FTE cohort = 209."
  }

  measure: offers_accepted {
    type: count
    filters: [current_stage: "hired"]
    description: "Offers accepted (the recruiting tables' hire; the person may not have started yet). Example: 2025 FTE cohort = 159; 40 accepted in Q3 2026 (filter Offer Accepted Date)."
  }

  # ^_ is a literal underscore: in a Looker filter value a bare _ matches any one character.
  measure: offers_declined {
    type: count
    filters: [current_stage: "offer^_declined"]
    description: "Offers the candidate declined. Example: 10 declined in Q3 2026 (filter Offer Answered Date)."
  }

  # Conversion from the previous stage, for a cohort of applications (filter Applied Date)

  measure: screen_rate {
    type: number
    value_format_name: percent_1
    sql: ${screened} / nullif(${applications}, 0) ;;
    description: "Share of applications that reached the screen. Example: 2025 FTE cohort = 25.2%."
  }

  measure: interview_rate {
    type: number
    value_format_name: percent_1
    sql: ${interviewed} / nullif(${screened}, 0) ;;
    description: "Share of screened applications that reached the interview. Example: 2025 FTE cohort = 32.9%."
  }

  measure: offer_rate {
    type: number
    value_format_name: percent_1
    sql: ${offered} / nullif(${interviewed}, 0) ;;
    description: "Share of interviewed applications that got an offer. Example: 2025 FTE cohort = 22.4%."
  }

  measure: acceptance_rate {
    label: "Offer Acceptance Rate"
    type: number
    value_format_name: percent_1
    sql: ${offers_accepted} / nullif(${offers_accepted} + ${offers_declined}, 0) ;;
    description: "Offers accepted divided by offers that got an answer (accepted or declined); offers still waiting are left out. For a period, filter Offer Answered Date. Example: Q3 2026 = 40 / (40 + 10) = 80.0%."
  }

  measure: funnel_conversion_rate {
    type: number
    value_format_name: percent_1
    sql: ${offers_accepted} / nullif(${applications}, 0) ;;
    description: "Share of applications that led to an accepted offer. Use a cohort old enough to have finished. Example: 2025 FTE cohort = 159 / 11,252 = 1.4%."
  }

  # Time to hire

  measure: median_time_to_hire_days {
    label: "Median Time to Hire (days)"
    type: median
    value_format_name: decimal_0
    sql: ${days_to_hire} ;;
    description: "Median days from the hired candidate applying to accepting the offer. For a period, filter Offer Accepted Date. Example: Q3 2026, 40 accepted FTE offers = 25 days."
  }

  measure: avg_time_to_hire_days {
    label: "Average Time to Hire (days)"
    type: average
    value_format_name: decimal_1
    sql: ${days_to_hire} ;;
    description: "Mean days from the hired candidate applying to accepting the offer; measures the candidate's experience, where time to fill measures the requisition's. For a period, filter Offer Accepted Date. Example: Q3 2026 = 26.2 days."
  }
}
