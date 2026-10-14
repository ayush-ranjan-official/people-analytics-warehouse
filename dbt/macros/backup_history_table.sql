{#
    Pre-hook for tables that hold the only copy of their history (snap_employees). In prod, copies the
    current table to <name>_prev before it's rebuilt, but only if the current table passes its own
    checks, so a bad build that went unnoticed for a night can't overwrite the last good copy.

    Restore: create or replace table analytics.snap_employees as select * from analytics.snap_employees_prev
    (BigQuery time travel, FOR SYSTEM_TIME AS OF, also reaches back 7 days.)
#}
{% macro backup_history_table() %}
    {%- if execute and target.name == 'prod' -%}
        {%- set current = load_relation(this) -%}
        {%- if current is not none -%}
            {%- set backup = this.incorporate(path={'identifier': this.identifier ~ '_prev'}) -%}
            {%- set check -%}
                select
                    (select count(*) from (
                        select employee_id from {{ current }} group by employee_id having countif(is_current) > 1))
                  + (select count(*) - count(distinct format('%t', (employee_id, valid_from))) from {{ current }})
                  + (select countif(valid_to <= valid_from) from {{ current }})
            {%- endset -%}
            {%- set problems = run_query(check).columns[0].values()[0] -%}
            {%- if problems == 0 -%}
                {%- do run_query("create or replace table " ~ backup ~ " as select * from " ~ current) -%}
                {{ log("Copied " ~ current ~ " to " ~ backup, info=true) }}
            {%- else -%}
                {%- do exceptions.warn(current ~ " fails its checks (" ~ problems ~ " problems), so " ~ backup
                                       ~ " is kept as the last good copy. Restore from it before the next build.") -%}
            {%- endif -%}
        {%- endif -%}
    {%- endif -%}
{% endmacro %}
