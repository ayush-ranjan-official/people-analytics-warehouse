-- Headcount runs up to the reporting date. An empty or truncated headcount (for example, a NULL
-- reporting date) would otherwise pass every other test, since there'd be nothing to fail on.

select
    reporting_date,
    (select max(snapshot_date) from {{ ref('fct_headcount_daily') }}) as headcount_through
from {{ ref('int_reporting_date') }}
where reporting_date is null
   or reporting_date is distinct from (select max(snapshot_date) from {{ ref('fct_headcount_daily') }})
