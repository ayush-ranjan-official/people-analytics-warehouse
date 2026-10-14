-- An employee is in exactly one job version on any day: no two versions may overlap.

select
    earlier.employee_id,
    earlier.job_history_id,
    later.job_history_id as overlapping_job_history_id
from {{ ref('dim_employee') }} as earlier
inner join {{ ref('dim_employee') }} as later
    on earlier.employee_id = later.employee_id
   and earlier.job_history_id < later.job_history_id
   and earlier.valid_from <= later.valid_to
   and later.valid_from <= earlier.valid_to
