# Text-to-SQL eval: holdout-documented-20261015-1201

- Model: claude-sonnet-5-5 (effort medium) through Claude Code 2.1.273, claude.ai (max)
- Context: eval/schema_context_original.md (sha256 919e3989455f); prompt sha256 8f8e7eed7f8b; questions sha256 cedfb2cb24d1
- Data as of 2026-10-14
- Isolation canary: passed. Its reply: "I can't read either file. I have no file-reading or shell tools in this session, so I can't open `canary.txt` in the working directory or `<repo>/eval/questions.yaml`. I won't guess at their contents. If you paste the contents here, I can work with them. If you run this somewhere with file access, ..."

**Score: 8/8 (100%)**

| difficulty | questions | pass | fail | sql_error | not_answered | harness_error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| easy | 2 | 2 | 0 | 0 | 0 | 0 |
| medium | 3 | 3 | 0 | 0 | 0 | 0 |
| hard | 3 | 3 | 0 | 0 | 0 | 0 |
| **all** | 8 | 8 | 0 | 0 | 0 | 0 |

## Misses

None.

## Passes with extra columns

Extra columns are ignored, so check that the matched column is the answer and not an alternative reading.

- h03: expected ['contractor_share'] [[0.03403565640194489]]; got ['contractors', 'total_working', 'contractor_share'] [[21, 617, 0.03403565640194489]]
- h07: expected ['team', 'attrition_rate'] [["Engineering - Platform", 0.15367496691928303]]; got ['team', 'exits', 'average_headcount', 'attrition_rate_pct'] [["Engineering - Data", 9, 53.20821917808219, 16.914679985582616]]
- h08: expected ['location', 'regretted_attrition_rate'] [["Bengaluru", 0.021004776428612534]]; got ['location', 'regretted_exits', 'average_headcount', 'regretted_attrition_rate'] [["Bengaluru", 4, 190.43287671232878, 0.021004776428612534]]
