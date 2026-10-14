-- Attrition rate for Q3 2026: FTE exits in the quarter divided by the average daily FTE headcount
-- over the quarter (not the headcount at the end of it).

with headcount as (
    select avg(daily) as average_headcount
    from (
        select snapshot_date, count(*) as daily
        from {{ ref('fct_headcount_daily') }}
        where is_fte and snapshot_date between date '2026-07-01' and date '2026-09-30'
        group by snapshot_date
    )
),

exits as (
    select
        count(*) as exits,
        countif(termination_type = 'voluntary') as voluntary_exits
    from {{ ref('fct_terminations') }}
    where termination_date between date '2026-07-01' and date '2026-09-30'
)

select
    exits.exits,
    exits.voluntary_exits,
    round(headcount.average_headcount, 1) as average_headcount,
    round(exits.exits / headcount.average_headcount * 100, 2) as attrition_rate_pct,
    round(exits.voluntary_exits / headcount.average_headcount * 100, 2) as voluntary_attrition_rate_pct
from exits, headcount
