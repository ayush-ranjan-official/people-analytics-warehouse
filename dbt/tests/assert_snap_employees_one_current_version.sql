-- No employee has more than one current version. (One deleted at the source has none.)

select employee_id, countif(is_current) as current_versions
from {{ ref('snap_employees') }}
group by employee_id
having countif(is_current) > 1
