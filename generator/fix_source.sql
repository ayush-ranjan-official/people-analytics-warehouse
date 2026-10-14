-- Source fixes for the data-quality issues the warehouse tests caught (generator/PLANTED_ISSUES.md).
-- Applied in the source system, so the corrections reach the warehouse through the normal sync as
-- row updates and deletes. Safe to re-run: each statement only touches rows that are still wrong.

-- 1. Department names keyed as "Engg - X" instead of "Engineering - X".
update hris.hris_departments
set department_name = replace(department_name, 'Engg - ', 'Engineering - ')
where department_name like 'Engg - %';

-- 2. Applications submitted twice: keep the first per candidate and requisition.
delete from hris.ats_applications as later
using hris.ats_applications as earlier
where later.req_id = earlier.req_id
  and later.candidate_id = earlier.candidate_id
  and later.application_id > earlier.application_id;

-- 3. Job records whose end date was keyed a year early: a record ends the day before the person's
--    next record starts, or on their exit date, or stays open.
update hris.hris_job_history as j
set effective_to = coalesce(n.next_from - 1, e.termination_date, date '9999-12-31')
from (
    select job_history_id,
           lead(effective_from) over (partition by employee_id order by effective_from) as next_from
    from hris.hris_job_history
) as n,
hris.hris_employees as e
where j.job_history_id = n.job_history_id
  and e.employee_id = j.employee_id
  and j.effective_to < j.effective_from;

-- 4. Exit records keyed with ids from the old HRIS, which match no employee. There's no person to
--    attach them to, so they're removed (in a real system: investigated first, then removed).
delete from hris.hris_terminations as t
where not exists (select 1 from hris.hris_employees as e where e.employee_id = t.employee_id);
