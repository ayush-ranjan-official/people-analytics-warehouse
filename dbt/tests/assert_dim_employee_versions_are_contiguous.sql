-- Each employee's job versions run without gaps from the hire date to the last day employed
-- (9999-12-31 while still employed). A gap would drop days from headcount without failing any
-- other test.

with versions as (
    select
        employee_id,
        valid_from,
        valid_to,
        hire_date,
        coalesce(termination_date, date '9999-12-31') as last_day,
        lead(valid_from) over (partition by employee_id order by valid_from) as next_from,
        row_number() over (partition by employee_id order by valid_from) as version_number
    from {{ ref('dim_employee') }}
)

select employee_id, valid_from, valid_to, next_from
from versions
where (version_number = 1 and valid_from != hire_date)
   or (next_from is not null and next_from != date_add(valid_to, interval 1 day))
   or (next_from is null and valid_to != last_day)
