# Planted data-quality issues

These rows are wrong on purpose, so the warehouse tests have real problems to catch.
They are written by `generator/planted_issues.py` and switched off with
`planted_issues.enabled: false` in `generator/config.yaml`.

Generated for data as of 2026-09-30.

| Issue | Table | Row | Detail |
| --- | --- | --- | --- |
| department_name_typo | `hris_departments` | `D02` | 'Engineering - Platform' stored as 'Engg - Platform' |
| department_name_typo | `hris_departments` | `D03` | 'Engineering - Data' stored as 'Engg - Data' |
| department_name_typo | `hris_departments` | `D04` | 'Engineering - Security' stored as 'Engg - Security' |
| duplicate_application | `ats_applications` | `A9000001` | duplicate of A1012842 |
| duplicate_application | `ats_applications` | `A9000002` | duplicate of A1014938 |
| duplicate_application | `ats_applications` | `A9000003` | duplicate of A1031136 |
| duplicate_application | `ats_applications` | `A9000004` | duplicate of A1031398 |
| duplicate_application | `ats_applications` | `A9000005` | duplicate of A1033391 |
| inverted_job_history | `hris_job_history` | `JH000499` | effective_to 2023-09-30 keyed as 2022-09-30 (effective_from 2023-04-01) |
| inverted_job_history | `hris_job_history` | `JH002144` | effective_to 2025-09-30 keyed as 2024-09-30 (effective_from 2025-04-01) |
| orphan_termination | `hris_terminations` | `T90001` | employee_id E04584 does not exist |
| orphan_termination | `hris_terminations` | `T90002` | employee_id E04752 does not exist |
| orphan_termination | `hris_terminations` | `T90003` | employee_id E06792 does not exist |
| orphan_termination | `hris_terminations` | `T90004` | employee_id E03320 does not exist |
