-- The dashboard's two exit sources agree: rpt_exits (one row per exit) grouped by day, department,
-- location and tenure band gives the same counts as rpt_workforce_daily. Each view assigns tenure bands
-- itself, so this catches the two drifting apart.

with from_exits as (
    select
        termination_date as snapshot_date,
        department_id,
        location,
        tenure_band,
        count(*) as exits,
        countif(termination_type = 'voluntary') as voluntary_exits,
        countif(regretted_flag) as regretted_exits
    from {{ ref('rpt_exits') }}
    where termination_date between date '{{ var("history_start_date") }}'
                               and (select reporting_date from {{ ref('int_reporting_date') }})
    group by 1, 2, 3, 4
),

from_daily as (
    select snapshot_date, department_id, location, tenure_band, exits, voluntary_exits, regretted_exits
    from {{ ref('rpt_workforce_daily') }}
    where exits > 0
)

select
    snapshot_date,
    department_id,
    location,
    tenure_band,
    from_exits.exits as exits_in_rpt_exits,
    from_daily.exits as exits_in_rpt_workforce_daily
from from_exits
full outer join from_daily
    using (snapshot_date, department_id, location, tenure_band)
where from_exits.exits is distinct from from_daily.exits
   or from_exits.voluntary_exits is distinct from from_daily.voluntary_exits
   or from_exits.regretted_exits is distinct from from_daily.regretted_exits
