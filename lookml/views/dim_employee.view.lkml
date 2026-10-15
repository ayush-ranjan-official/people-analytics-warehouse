# One row per employee per job version (one row per HRIS job-history record). Facts carry the
# employee_key of the version in effect on the event date. A person has several rows: filter
# is_current for people employed today, and count distinct person_id to count people across rehires.

view: dim_employee {
  sql_table_name: `@{bq_project}.@{bq_dataset}.dim_employee` ;;
  label: "Employee"

  dimension: employee_key {
    primary_key: yes
    hidden: yes
    type: number
    value_format_name: id
    sql: ${TABLE}.employee_key ;;
    description: "Surrogate key for this job version. Facts join on it."
  }

  dimension: job_history_id {
    hidden: yes
    type: string
    sql: ${TABLE}.job_history_id ;;
    description: "The HRIS job-history record this version comes from."
  }

  # Identity

  dimension: employee_id {
    group_label: "Identity"
    type: string
    sql: ${TABLE}.employee_id ;;
    description: "HRIS employee id, e.g. 'E10001'. A person who leaves and is rehired gets a new one."
  }

  dimension: person_id {
    group_label: "Identity"
    type: string
    sql: ${TABLE}.person_id ;;
    description: "The same person across rehires (the employee_id of their first employment record, matched on email). Count distinct person_id to count people."
  }

  dimension: first_name {
    group_label: "Identity"
    type: string
    sql: ${TABLE}.first_name ;;
    description: "First name (synthetic)."
  }

  dimension: last_name {
    group_label: "Identity"
    type: string
    sql: ${TABLE}.last_name ;;
    description: "Last name, as it is today (synthetic)."
  }

  dimension: email {
    group_label: "Identity"
    type: string
    sql: ${TABLE}.email ;;
    description: "Work email, lower case (synthetic)."
  }

  # Employment record

  dimension: employment_type {
    type: string
    sql: ${TABLE}.employment_type ;;
    description: "'fte', 'contractor' or 'intern'. Headcount and attrition count fte only."
  }

  dimension_group: hire {
    type: time
    timeframes: [raw, date, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.hire_date ;;
    description: "First day of this employment record. Tenure runs from here, so a rehire's tenure restarts."
  }

  dimension_group: termination {
    type: time
    timeframes: [raw, date, month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.termination_date ;;
    description: "Last day employed on this record. Null while still employed."
  }

  # Job version

  dimension_group: valid_from {
    group_label: "Job version"
    type: time
    timeframes: [raw, date]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.valid_from ;;
    description: "First day this version applies (inclusive)."
  }

  dimension_group: valid_to {
    group_label: "Job version"
    type: time
    timeframes: [raw, date]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.valid_to ;;
    description: "Last day this version applies (inclusive). 9999-12-31 for a version still in effect."
  }

  dimension: is_latest_version {
    group_label: "Job version"
    type: yesno
    sql: ${TABLE}.is_latest_version ;;
    description: "True for the last version of each employment record, including people who have left."
  }

  dimension: is_current {
    group_label: "Job version"
    type: yesno
    sql: ${TABLE}.is_current ;;
    description: "True for the version in effect today for people still employed (one row per active employee, all employment types)."
  }

  dimension: change_reason {
    group_label: "Job version"
    type: string
    sql: ${TABLE}.change_reason ;;
    description: "Why this version started: hire, rehire, promotion, transfer, manager_change, location_change, merit_increase or salary_adjustment."
  }

  # Job in this version

  dimension: position_id {
    type: string
    sql: ${TABLE}.position_id ;;
    description: "The seat held in this version."
  }

  dimension: department_id {
    hidden: yes
    type: string
    sql: ${TABLE}.department_id ;;
    description: "Department or team in this version. Join key to dim_org."
  }

  dimension: manager_id {
    type: string
    sql: ${TABLE}.manager_id ;;
    description: "employee_id of the manager in this version. Null only for the CEO."
  }

  dimension: location {
    type: string
    sql: ${TABLE}.location ;;
    description: "Work location: Bengaluru, London, New York, Remote or Singapore."
  }

  dimension: job_level {
    type: string
    sql: ${TABLE}.job_level ;;
    description: "Job level, 'L1' (entry) to 'L7' (most senior)."
  }

  dimension: job_level_number {
    type: number
    sql: ${TABLE}.job_level_number ;;
    description: "The job level as a number, 1 to 7, for comparisons such as job level number >= 5."
  }

  # Salary is hidden from the field picker and gated by the can_see_salary access grant (defined in
  # the model): in a real deployment only users given that grant, such as compensation analysts,
  # could query pay at all. salary_local is in the row's currency: never sum or average it across
  # currencies.

  dimension: salary_local {
    hidden: yes
    required_access_grants: [can_see_salary]
    type: number
    value_format_name: decimal_0
    sql: ${TABLE}.salary_local ;;
    description: "Annual base salary in the version's currency. Never sum or average across currencies."
  }

  dimension: currency {
    hidden: yes
    required_access_grants: [can_see_salary]
    type: string
    sql: ${TABLE}.currency ;;
    description: "ISO currency of salary_local: INR, GBP, SGD or USD (follows the location)."
  }
}
