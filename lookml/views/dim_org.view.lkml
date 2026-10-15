# One row per department or team (11 rows). Engineering is split into three teams (Platform, Data,
# Security) under a parent row named 'Engineering' that holds only the head of Engineering, so whole
# functions are grouped with division_name.

view: dim_org {
  sql_table_name: `@{bq_project}.@{bq_dataset}.dim_org` ;;
  label: "Department"

  dimension: department_id {
    primary_key: yes
    hidden: yes
    type: string
    sql: ${TABLE}.department_id ;;
    description: "HRIS department id, e.g. 'D01'. Join key."
  }

  dimension: department_name {
    label: "Department"
    type: string
    sql: ${TABLE}.department_name ;;
    description: "Department or team name, e.g. 'Sales' or 'Engineering - Data'. Teams are named '<Department> - <Team>'. 'Engineering' alone is the parent row with one person: for all of Engineering use Division."
  }

  dimension: division_name {
    label: "Division"
    type: string
    sql: ${TABLE}.division_name ;;
    description: "The top-level department this row rolls up to (the parent's name for a team, its own name otherwise). Use it to group a whole function."
  }

  dimension: parent_department_id {
    hidden: yes
    type: string
    sql: ${TABLE}.parent_department_id ;;
    description: "The department a team belongs to. Null for a top-level department."
  }

  dimension: parent_department_name {
    label: "Parent Department"
    type: string
    sql: ${TABLE}.parent_department_name ;;
    description: "Name of the parent department. Null for a top-level department."
  }

  dimension: cost_center {
    type: string
    sql: ${TABLE}.cost_center ;;
    description: "Finance cost centre code."
  }

  dimension: is_team {
    type: yesno
    sql: ${TABLE}.is_team ;;
    description: "True if no other department sits under it, i.e. people are assigned here. False only for a parent that groups teams (Engineering)."
  }

  dimension: source_department_name {
    group_label: "Data quality"
    type: string
    sql: ${TABLE}.source_department_name ;;
    description: "The name as stored in the HRIS, before any correction."
  }

  dimension: name_corrected_in_warehouse {
    group_label: "Data quality"
    type: yesno
    sql: ${TABLE}.name_corrected_in_warehouse ;;
    description: "True if department_name was corrected by the warehouse's mapping seed. Should be false everywhere; true means the HRIS needs fixing."
  }
}
