# Project manifest. The BigQuery project and dataset are constants so every view can be pointed at
# another dataset (for example the dev build, dbt_dev) by changing one value.

project_name: "people_analytics"

constant: bq_project {
  value: "people-analytics-demo-2610"
}

constant: bq_dataset {
  value: "analytics"
}
