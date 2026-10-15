# HR overview: headcount, attrition, tenure and recruiting on one page.
# The default date range is the four complete quarters to 2026-09-30 (a Looker range excludes its end
# date); the data runs to the reporting date, so a partial quarter would understate its rates.
# Each tile maps the dashboard filters onto its own explore's fields. The recruiting tables have no
# work location, so the recruiting tiles don't listen to Location, and they are pinned to FTE openings.

- dashboard: hr_overview
  title: "HR Overview - Nodewell Labs (synthetic data)"
  description: "Headcount, attrition, tenure and recruiting for Nodewell Labs, a synthetic company. Formulas follow docs/metric-definitions.md."
  layout: newspaper
  preferred_viewer: dashboards-next

  filters:
  - name: Division
    title: Division
    type: field_filter
    default_value: ""
    allow_multiple_values: true
    required: false
    ui_config:
      type: tag_list
      display: popover
    model: people_analytics
    explore: headcount
    listens_to_filters: []
    field: dim_org.division_name

  - name: Location
    title: Location
    type: field_filter
    default_value: ""
    allow_multiple_values: true
    required: false
    ui_config:
      type: tag_list
      display: popover
    model: people_analytics
    explore: headcount
    listens_to_filters: []
    field: fct_headcount_daily.location

  - name: Date range
    title: Date range
    type: field_filter
    default_value: "2025-10-01 to 2026-10-01"
    allow_multiple_values: true
    required: false
    ui_config:
      type: advanced
      display: popover
    model: people_analytics
    explore: headcount
    listens_to_filters: []
    field: fct_headcount_daily.snapshot_date

  elements:

  # Headcount explore. Average headcount is the attrition denominator; each exit is joined to the
  # leaver's last headcount row, so exits and headcount always cover the same people. Days are
  # counted from the rows selected, so a slice with no FTEs on some days (a small division in one
  # location, say) shows a higher average than the daily mean, and a lower rate.

  - name: avg_headcount_by_month
    title: Average FTE headcount by month
    note_text: "Average of the daily FTE headcount. Days with no FTEs in the filtered slice are left out, so a very small slice can read high."
    note_display: hover
    model: people_analytics
    explore: headcount
    type: looker_line
    fields: [fct_headcount_daily.snapshot_month, fct_headcount_daily.avg_headcount]
    sorts: [fct_headcount_daily.snapshot_month]
    limit: 500
    listen:
      Division: dim_org.division_name
      Location: fct_headcount_daily.location
      Date range: fct_headcount_daily.snapshot_date
    row: 0
    col: 0
    width: 12
    height: 6

  - name: quarterly_attrition_rate
    title: Quarterly attrition rate, voluntary vs involuntary
    note_text: "Exits in the quarter / average FTE headcount in the quarter, not annualised. Retirements count in the total only."
    note_display: hover
    model: people_analytics
    explore: headcount
    type: looker_column
    fields: [fct_headcount_daily.snapshot_quarter, fct_headcount_daily.attrition_rate,
      fct_headcount_daily.voluntary_attrition_rate, fct_headcount_daily.involuntary_attrition_rate]
    sorts: [fct_headcount_daily.snapshot_quarter]
    limit: 500
    listen:
      Division: dim_org.division_name
      Location: fct_headcount_daily.location
      Date range: fct_headcount_daily.snapshot_date
    row: 0
    col: 12
    width: 12
    height: 6

  # Attrition explore: one row per FTE exit, filtered on the exit date.

  - name: top_exit_reasons
    title: Top exit reasons
    model: people_analytics
    explore: attrition
    type: looker_bar
    fields: [fct_terminations.reason, fct_terminations.leavers]
    sorts: [fct_terminations.leavers desc]
    limit: 10
    listen:
      Division: dim_org.division_name
      Location: fct_terminations.location
      Date range: fct_terminations.termination_date
    row: 6
    col: 0
    width: 12
    height: 6

  - name: regretted_exits_by_department
    title: Regretted exits by department
    note_text: "Regretted: voluntary leavers the manager wanted to keep."
    note_display: hover
    model: people_analytics
    explore: attrition
    type: looker_bar
    fields: [dim_org.department_name, fct_terminations.regretted_leavers, fct_terminations.voluntary_leavers]
    sorts: [fct_terminations.regretted_leavers desc]
    limit: 500
    listen:
      Division: dim_org.division_name
      Location: fct_terminations.location
      Date range: fct_terminations.termination_date
    row: 6
    col: 12
    width: 12
    height: 6

  # Headcount on single days: FTEs at each quarter end in the range, split by tenure on that day.

  - name: tenure_distribution
    title: Tenure distribution of FTEs at each quarter end
    note_text: "Tenure from the current employment record's hire date, so a rehire's tenure restarts."
    note_display: hover
    model: people_analytics
    explore: headcount
    type: looker_column
    fields: [fct_headcount_daily.snapshot_quarter, fct_headcount_daily.tenure_band, fct_headcount_daily.headcount]
    pivots: [fct_headcount_daily.tenure_band]
    filters:
      dim_date.is_quarter_end: "Yes"
    sorts: [fct_headcount_daily.snapshot_quarter, fct_headcount_daily.tenure_band]
    limit: 500
    stacking: normal
    listen:
      Division: dim_org.division_name
      Location: fct_headcount_daily.location
      Date range: fct_headcount_daily.snapshot_date
    row: 12
    col: 0
    width: 12
    height: 6

  # Recruiting explore, FTE openings only.

  - name: time_to_hire_by_department
    title: Time to hire by department (days)
    note_text: "Days from the hired candidate applying to accepting the offer, for FTE offers accepted in the date range."
    note_display: hover
    model: people_analytics
    explore: recruiting
    type: looker_bar
    fields: [dim_org.department_name, fct_application_funnel.median_time_to_hire_days,
      fct_application_funnel.avg_time_to_hire_days]
    filters:
      fct_requisitions.employment_type: fte
    sorts: [fct_application_funnel.median_time_to_hire_days desc]
    limit: 500
    listen:
      Division: dim_org.division_name
      Date range: fct_application_funnel.hired_at_date
    row: 12
    col: 12
    width: 12
    height: 6

  - name: funnel_conversion_by_stage
    title: Funnel conversion by stage
    note_text: "Applications submitted in the date range. Recent ones are still in progress, so pick a range that ended a few months ago for final conversion rates."
    note_display: hover
    model: people_analytics
    explore: recruiting
    type: looker_funnel
    fields: [fct_application_funnel.applications, fct_application_funnel.screened,
      fct_application_funnel.interviewed, fct_application_funnel.offered,
      fct_application_funnel.offers_accepted]
    filters:
      fct_requisitions.employment_type: fte
    limit: 500
    listen:
      Division: dim_org.division_name
      Date range: fct_application_funnel.applied_date
    row: 18
    col: 0
    width: 12
    height: 6

  - name: applications_and_hires_by_source
    title: Applications and hires by source
    note_text: "Applications submitted in the date range, the offers they led to, and the people who have started."
    note_display: hover
    model: people_analytics
    explore: recruiting
    type: looker_grid
    fields: [fct_application_funnel.source, fct_application_funnel.applications,
      fct_application_funnel.offers_accepted, fct_hires.hires,
      fct_application_funnel.funnel_conversion_rate]
    filters:
      fct_requisitions.employment_type: fte
    sorts: [fct_application_funnel.applications desc]
    limit: 500
    listen:
      Division: dim_org.division_name
      Date range: fct_application_funnel.applied_date
    row: 18
    col: 12
    width: 12
    height: 6
