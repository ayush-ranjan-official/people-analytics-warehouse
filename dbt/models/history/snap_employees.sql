{{ config(materialized='table', pre_hook="{{ backup_history_table() }}") }}

{#-
Grain: one row per version of an employee record (SCD Type 2 history of stg_hris__employees).

hris_employees is updated in place, so its history only exists if the warehouse keeps it. dbt's
snapshot command needs MERGE, which the BigQuery sandbox blocks, so this model gets the same
result by rebuilding itself from its previous version plus whatever changed since.

- A change to a tracked column closes the open version and opens a new one at the record's
  updated_at. If updated_at hasn't moved past the last recorded change, the run time is used instead,
  so versions never overlap or run backwards.
- A record deleted at the source has its open version closed at the run time. If it comes back, it
  opens a new version.
- A newly tracked column is filled in on the open versions without starting new ones; closed
  versions have it as NULL. Removing a column from `tracked` drops its history for good.

This table is the only copy of the history; in prod the pre-hook copies it to snap_employees_prev
first. Don't add partition_by or cluster_by (dbt drops a table whose partitioning changed before
rebuilding it), and don't full-refresh it on purpose.
-#}

{%- set tracked = ['first_name', 'last_name', 'email', 'hire_date', 'termination_date',
                   'employee_status', 'employment_type'] -%}
{%- set previous = load_relation(this) -%}
{%- set previous_columns = tracked -%}
{%- set staged_types = {} -%}

{%- if execute and previous is not none -%}
    {%- set counts = run_query(
        "select (select count(*) from " ~ ref('stg_hris__employees') ~ "), "
        ~ "(select count(distinct employee_id) from " ~ ref('stg_hris__employees') ~ "), "
        ~ "(select countif(is_current) from " ~ previous ~ ")").rows[0] -%}
    {%- set staged, staged_ids, open_count = counts[0], counts[1], counts[2] -%}
    {#- A duplicated key would open two current versions that then become permanent history. -#}
    {%- if staged != staged_ids -%}
        {{ exceptions.raise_compiler_error(
            "snap_employees: stg_hris__employees has " ~ staged ~ " rows but only " ~ staged_ids
            ~ " distinct employee_ids. Refusing to rebuild.") }}
    {%- endif -%}
    {#- Absent records get closed, so an empty or truncated staging run would close everyone. -#}
    {%- if staged < 0.9 * open_count -%}
        {{ exceptions.raise_compiler_error(
            "snap_employees: stg_hris__employees has " ~ staged ~ " rows but " ~ open_count
            ~ " versions are open. Refusing to rebuild.") }}
    {%- endif -%}
    {%- set previous_columns = adapter.get_columns_in_relation(previous) | map(attribute='name') | list -%}
    {%- for col in adapter.get_columns_in_relation(ref('stg_hris__employees')) -%}
        {%- do staged_types.update({col.name: col.data_type}) -%}
    {%- endfor -%}
{%- endif -%}
{%- set compared = tracked | select('in', previous_columns) | list -%}

with current_records as (
    select
        employee_id,
        {{ tracked | join(',\n        ') }},
        updated_at
    from {{ ref('stg_hris__employees') }}
)

{%- if previous is none %}

-- First build: every record opens its first version, valid from its last update.
select
    employee_id,
    {{ tracked | join(',\n    ') }},
    updated_at as valid_from,
    cast(null as timestamp) as valid_to,
    true as is_current
from current_records

{%- else %}

, previous_versions as (
    select
        employee_id,
        {%- for column in tracked %}
        {{ column if column in previous_columns else 'cast(null as ' ~ staged_types[column] ~ ') as ' ~ column }},
        {%- endfor %}
        valid_from,
        valid_to,
        is_current
    from {{ previous }}
),

open_versions as (
    select * from previous_versions where is_current
),

last_changes as (
    select employee_id, max(coalesce(valid_to, valid_from)) as last_change
    from previous_versions
    group by employee_id
),

changed as (
    select
        current_records.*,
        if(last_changes.last_change is null or current_records.updated_at > last_changes.last_change,
           current_records.updated_at,
           greatest(current_timestamp(), timestamp_add(last_changes.last_change, interval 1 microsecond))
        ) as changed_at
    from current_records
    left join open_versions
        on current_records.employee_id = open_versions.employee_id
    left join last_changes
        on current_records.employee_id = last_changes.employee_id
    where open_versions.employee_id is null
       or to_json_string(struct({% for c in compared %}open_versions.{{ c }}{{ ', ' if not loop.last }}{% endfor %}))
       != to_json_string(struct({% for c in compared %}current_records.{{ c }}{{ ', ' if not loop.last }}{% endfor %}))
)

-- Closed history is kept as it is.
select * from previous_versions where not is_current

union all

-- Open versions stay open unless the record changed (closed where the new version starts) or
-- disappeared from the source (closed now).
select
    open_versions.employee_id,
    {%- for column in tracked %}
    {%- if column in previous_columns %}
    open_versions.{{ column }},
    {%- else %}
    if(changed.employee_id is null, current_records.{{ column }}, open_versions.{{ column }}) as {{ column }},
    {%- endif %}
    {%- endfor %}
    open_versions.valid_from,
    case
        when changed.employee_id is not null then changed.changed_at
        when current_records.employee_id is null then current_timestamp()
    end as valid_to,
    changed.employee_id is null and current_records.employee_id is not null as is_current
from open_versions
left join changed
    on open_versions.employee_id = changed.employee_id
left join current_records
    on open_versions.employee_id = current_records.employee_id

union all

-- Each change opens a new version.
select
    employee_id,
    {{ tracked | join(',\n    ') }},
    changed_at as valid_from,
    cast(null as timestamp) as valid_to,
    true as is_current
from changed

{%- endif %}
