# Data quality: caught at the source, fixed at the source

The source system ships with four known problems ([generator/PLANTED_ISSUES.md](../generator/PLANTED_ISSUES.md)),
planted so the warehouse tests have something real to catch. All four were caught by tests on the raw
and staging layers, before any mart was built. They were then fixed **in the source system**, not
patched over in the warehouse, so every downstream consumer gets the corrected data.

## What the tests caught

First `dbt build` against the raw data (2026-10-14): 28 checks passed, 4 failed, and 8 steps were
skipped because they depend on the failing tests.

| Problem in the source | Test that caught it | Failing rows |
| --- | --- | --- |
| Applications submitted twice (same candidate, same requisition) | `dbt_utils.unique_combination_of_columns` on `ats.applications` (req_id, candidate_id) | 5 |
| Job records whose end date was keyed a year early | `dbt_utils.expression_is_true` on `hris.job_history`: `effective_to >= effective_from` | 2 |
| Exit records keyed with ids from the old HRIS | `relationships` from `hris.terminations.employee_id` to `hris.employees` | 4 |
| "Engineering - X" teams stored as "Engg - X" | `accepted_values` on `stg_hris__departments.department_name` | 3 |

## How each was fixed

The fixes are a reviewed SQL patch, [generator/fix_source.sql](../generator/fix_source.sql), applied with
`make fix-source` (a dry run that rolls back) and then `make fix-source APPLY=1`:

1. **Department names:** `Engg - ` is replaced with `Engineering - ` in the HRIS. `dim_org` also keeps
   a mapping seed (`department_name_corrections`) as a safety net, and flags any row it had to correct
   (`name_corrected_in_warehouse`), so a recurrence is visible rather than silently patched.
2. **Duplicate applications:** the later submission is deleted, keeping the first per candidate and
   requisition. Staging also de-duplicates on that business key, so a new double submission can't
   inflate the funnel before it's fixed.
3. **Job-record end dates:** each wrong `effective_to` is set to the day before the person's next record
   starts. After the fix, the source matched a clean regeneration of the data row for row, so the
   patch restored the true dates rather than plausible ones.
4. **Orphan exit records:** removed. No employee matches them, so they can't be attributed to anyone.

Because the fixes are ordinary updates and deletes in the source, they reach BigQuery through the
normal sync: Fivetran records the deletes as `_fivetran_deleted = true`, and staging filters those out.
The generator was then switched to stop planting the issues (`planted_issues.enabled: false`), so
regenerated data is clean too.

## Result

`dbt build` after the fix: every check passed. The same tests stay in place, so a recurrence fails the
nightly build instead of reaching a dashboard.
