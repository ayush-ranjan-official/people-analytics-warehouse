{#
    Checks a landed table against the columns and types the models rely on (docs/data-contract-hris.md).

    column_types: {column: BigQuery type} for every column the warehouse reads.
    change: 'breaking' returns a row for each contract column that is missing or has another type;
            a rename shows up here as a missing column. 'additive' returns a row for each column that
            isn't in the contract (Fivetran adds new source columns by itself); run it with severity warn.

    Problems are also printed, so the log says which column changed and how.
#}
{% test schema_contract(model, column_types, change='breaking') %}
    {%- if change not in ['breaking', 'additive'] -%}
        {{ exceptions.raise_compiler_error("schema_contract: change must be 'breaking' or 'additive', got " ~ change) }}
    {%- endif -%}
    {#- The BigQuery API still reports some types by their legacy names. -#}
    {%- set aliases = {'INTEGER': 'INT64', 'BOOLEAN': 'BOOL', 'FLOAT': 'FLOAT64'} -%}
    {%- set expected = {} -%}
    {%- for name, dtype in column_types.items() -%}
        {%- do expected.update({name | lower: aliases.get(dtype | upper, dtype | upper)}) -%}
    {%- endfor -%}
    {%- set actual = {} -%}
    {%- if execute -%}
        {%- for col in adapter.get_columns_in_relation(model) -%}
            {%- do actual.update({col.name | lower: aliases.get(col.dtype | upper, col.dtype | upper)}) -%}
        {%- endfor -%}
    {%- endif -%}

    {%- set problems = [] -%}
    {%- if change == 'breaking' -%}
        {%- for name, dtype in expected.items() if actual.get(name) != dtype -%}
            {%- do problems.append((name, dtype, actual.get(name, 'MISSING'))) -%}
        {%- endfor -%}
    {%- else -%}
        {%- for name, dtype in actual.items() if name not in expected -%}
            {%- do problems.append((name, 'NOT IN CONTRACT', dtype)) -%}
        {%- endfor -%}
    {%- endif -%}

    {%- if execute and problems -%}
        {%- for name, want, got in problems -%}
            {{ log(model.identifier ~ "." ~ name ~ ": contract says " ~ want ~ ", table has " ~ got, info=true) }}
        {%- endfor -%}
    {%- endif -%}

    {%- for name, want, got in problems %}
    select '{{ name }}' as column_name, '{{ want }}' as contract_type, '{{ got }}' as landed_type
    {% if not loop.last %}union all{% endif %}
    {%- else %}
    select cast(null as string) as column_name, cast(null as string) as contract_type,
        cast(null as string) as landed_type
    from (select 1) where false
    {%- endfor %}
{% endtest %}
