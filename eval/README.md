# Text-to-SQL eval: can an agent answer HR questions from these docs?

The warehouse is documented so that an AI agent, not only an analyst, can query it: every mart states
its grain, joins and traps, and [docs/metric-definitions.md](../docs/metric-definitions.md) defines each
metric with a query. This eval puts a number on that claim. Claude writes one BigQuery query per HR
question, given either the bare schema or the documentation, and the query's result is checked against
a gold query verified by hand.

## Results

Same 20 questions, same prompt and model, three contexts:

| Run | Context | Easy (6) | Medium (9) | Hard (5) | Total |
| --- | --- | ---: | ---: | ---: | ---: |
| A, bare | [schema_bare.md](schema_bare.md): table and column names and types | 3 | 2 | 2 | **7/20** |
| B, documented | [schema_context_original.md](schema_context_original.md): the docs as first written | 5 | 8 | 4 | **17/20** |
| C, improved | [schema_context.md](schema_context.md): after fixing the docs behind Run B's misses | 6 | 9 | 5 | **20/20** |

Results: [A](results/bare-20261015-1158.summary.md), [B](results/documented-20261015-1159.summary.md),
[C](results/improved-20261015-1200.summary.md), each with a `.jsonl` holding every prompt hash, reply,
query and result. Run A ran on data as of 2026-10-13 and Runs B and C on 2026-10-14 (the source
advances one business day per nightly run); see [Limitations](#limitations).

- **Bare to documented, 7 to 17.** 12 of Run A's 13 misses counted contractors and interns, which the
  schema alone can't tell you to leave out (q01 gave 608 instead of 574 FTEs); q16 also used its own
  definition of first-year attrition. The 13th, q08, measured time to hire by start date.
- **Documented to improved, 17 to 20.** These are the same 20 questions the doc fixes were chosen from,
  so Run C is an in-sample score. Two of the three gains come from the fixes described below (q04, q16);
  the third (q08) is probably chance.
- **Held-out check.** Eight new questions, written after the fixes, ask for the other side of each new
  rule: contractors and interns by name, Engineering's teams, and rates per group that need CTEs like
  the attrition example ([questions_holdout.yaml](questions_holdout.yaml)). The fixed docs didn't
  make the model over-apply them, but the original docs did just as well, so the set shows no gain
  from the fixes either:

  | Context | Easy (2) | Medium (3) | Hard (3) | Total |
  | --- | ---: | ---: | ---: | ---: |
  | bare | 2 | 2 | 0 | [4/8](results/holdout-bare-20261015-1201.summary.md) |
  | documented, original | 2 | 3 | 3 | [8/8](results/holdout-documented-20261015-1201.summary.md) |
  | improved | 2 | 3 | 3 | [8/8](results/holdout-improved-20261015-1201.summary.md) |

Every pass and miss above was read by hand, not only the score. No answer hedged (put one reading in
the answer column and another in an extra column), and every miss is a real error rather than a
comparator mistake.

## Three failures and what fixed them

### 1. First-year attrition (q16): the documented example didn't run

*What was first-year attrition in 2025, meaning the attrition rate among employees with less than a
year of tenure?*

Run B failed with `No matching signature for operator * Argument types: INT64, STRUCT<exits INT64>`,
in both rounds. The model had followed the attrition example in the docs, which named a CTE `exits`
with a column `exits` and ended `select exits ... from exits, headcount`. In BigQuery a bare name that
matches a table in the `from` clause means that table's whole row, so `100 * exits` multiplies by a
STRUCT. The documented query itself failed when run.

**Fix:** the example now uses `period_exits` and `period_headcount`, qualifies its columns and says
why in one sentence. In Run C the model named its CTEs the same way and returned 27 exits over 139.85
average first-year FTEs, 19.31%, the gold answer. The bug only bites when a CTE and its column share
a name: held-out h08 used CTEs named `exits` and `headcount` with the original docs and passed.

### 2. Open requisitions (q04): the FTE rule was too narrow

*How many open requisitions do we have right now?*

The docs said "headcount, attrition and hiring metrics count full-time employees". The model decided
a plain count of openings isn't a hiring metric and counted contractor openings too: 19 instead of 15.
In round 1 it also put the FTE count in a second column "for reference". The comparator ignores extra
columns, so it matched the 15 and scored a pass, which reading the row overturned
([results/reviews.yaml](results/reviews.yaml)). In round 2 the prompt asks for one answer, so the
model returned 19 alone and described the FTE reading in a comment: a plain fail.

**Fix:** the rule now covers every workforce and recruiting figure, plain counts of requisitions
included, unless contractors or interns are asked for by name, and `fct_requisitions` says the same.
Run C returned 15 with the comment "Counts FTE openings only, per the warehouse rule for requisition
figures". The exception holds: held-out h02 (open contractor requisitions) and h06 (contractors who
left) were answered correctly with both versions of the docs.

### 3. Largest and highest-attrition department (q02, q18): a fix that was reverted

*Which department has the most employees today?* and *Which department had the highest attrition
rate in fiscal year 2026?* (the round 1 wording).

In round 1, Run B grouped by `dim_org.department_name`, which splits Engineering into three teams,
and answered Sales with 121 people and Engineering - Security with 18.79%. The gold answers were at
division level: Engineering with 203, Product with 18.58%. The first fix redefined "department" in
the docs to mean `division_name`, and Run C then passed both.

Review showed that fix made the docs agree with the gold, not with the rest of the repo. In LookML,
Data Studio and [docs/ad-hoc-requests.md](../docs/ad-hoc-requests.md), Department is `department_name`
(teams for Engineering) and Division is `division_name`, so the model's reading was reasonable. The
question was ambiguous; the docs weren't wrong. The redefinition was reverted: the docs now describe
both levels and name their Looker and Data Studio fields, and q02 and q18 ask for the division. Both
pass in Runs B and C, and they no longer count toward the gain from B to C.

**Run B's other miss** (q08, median time to hire) was a SQL error: `count(*)` next to
`percentile_cont(...) over ()`. Round 1's Run B answered it correctly with `approx_quantiles`. Run C
used `select distinct percentile_cont(...) over ()`, the form a note added to the tenure example
shows, so the note may have helped, but one sample can't tell that from chance.

## Round 1, and what changed after it

The first pass ([results/round1/](results/round1/)) scored 7/20 bare, 17/20 documented (16/20 after
reading q04) and 20/20 improved. A review of that pass led to these changes before the runs above:

- **"Department":** the redefinition was reverted and q02 and q18 reworded, as described above.
- **Hedges:** the prompt now asks for one answer with other readings in a SQL comment, each summary
  lists the passes that have extra columns, and `results/reviews.yaml` records verdicts from reading
  the results, which the summary prints next to the harness's score.
- **In-sample score:** the held-out set was added, and the original docs were frozen as
  `schema_context_original.md` so Run B can be repeated after the docs move on.
- **Questions:** q08's tolerance was 0.5 days, so a mean of 26.6 would have passed as the median of 27;
  it's now 0.05. q20 was labelled hard but, with the docs, is the worked funnel example grouped by
  source, so it's now medium. q06's gold gained the FTE filter it should have had (same answer, 91).
- **Harness:** a CLI failure, an unreachable BigQuery or an empty gold result is now a
  `harness_error`, listed apart with the run exiting 1, rather than looking like the model's miss;
  the CLI's environment is an allowlist; saved replies don't carry the local path.

All three runs were then repeated. Round 1's files are kept because the doc fixes came from them, but
its scores aren't comparable with the table above: two questions were worded differently, the prompt
had no one-answer line, the difficulty labels were 6, 8 and 6, and its Run C context still had the
department rule that was later reverted. Its files name `eval/schema_context.md` for both B and C;
the hashes tell them apart, and Run B's (919e3989455f) is today's `schema_context_original.md`.

## Method

**Questions.** [questions.yaml](questions.yaml) holds 20 questions (6 easy, 9 medium, 5 hard), each
with a `tests` line naming the trap it checks (FTE filter, reporting date, division versus team, the
job version valid on a past date, people versus exit events, fiscal year, offer accepted versus start
date) and a gold query. Every gold query was run on BigQuery and checked against a second query
written a different way (`dim_employee` versions with `dim_date` day counts instead of
`fct_headcount_daily`, `percentile_cont`, the anniversary rule for first-year tenure,
`hired_employee_id`). The 20 main questions were also checked against SQL written from the question
text before reading the gold. All matched. The answer and the usual wrong answers are recorded under
each question. No single-value answer appears in the documented context, but eight questions (q01,
q03, q07, q10, q11, q13, q14, q20) are a worked example on another date or cohort, so the docs help
them most.

**Model.** `claude-sonnet-5-5` at medium effort through the Claude Code CLI (2.1.273) on the claude.ai
subscription ([decision D8](../docs/decisions.md)). Each question is one `claude -p` call from a fresh
empty temp directory outside the repo, with no tools, MCP servers, settings, plugins, hooks or
CLAUDE.md, and a fixed system prompt. The input is [prompt.md](prompt.md), the context file and the
question. The harness refuses to run if `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set or the CLI
isn't on a claude.ai login, and passes the CLI only an allowlist of environment variables. In the runs
above every call took one attempt and one turn, 3 to 13 seconds.

**Isolation canary.** Each run starts by asking the model, the same way, for a random token in a file
in its working directory and for the first line of `questions.yaml`. It passes only if the model used
no tools and the reply contains neither; a failed canary stops the run. It passed before every run.

**Execution.** The reply's first SQL block is dry-run on BigQuery and run only if BigQuery reports a
SELECT, with `maximum_bytes_billed` of 1 GiB and a timeout. The gold query runs at the same moment on
the same data, so questions about "today" are judged consistently as the source advances. The largest
query billed 94 MB.

**Comparison.** Values are compared, not column names. Each gold column must be matched by its own
column of the generated result, row for row; extra columns are ignored. Counts must match exactly;
days, years and average headcount within 0.05; rates within 0.0005, as a fraction or a percentage
(0.001 for first-year attrition, where the end of "a year" is a matter of convention). Row order
counts only for the ranking question (q20). The full rule is in [run_eval.py](run_eval.py)'s
docstring. Because extra columns are ignored, a hedged answer can pass, which is why the summaries
list passes with extra columns for a reader to check.

**Statuses.** `pass`; `fail` (ran, wrong result); `sql_error` (not a SELECT, or BigQuery rejected it);
`not_answered` (no SQL in the reply); `harness_error` (the CLI or BigQuery failed, or the gold query
did: not the model's doing, and the run exits 1). Everything but `pass` counts as a miss.

## Limitations

- **Small and single-sample.** 20 questions plus 8 held out, one model at one effort, one answer per
  question per run. A single miss moves a score by 5 points, and q08 shows that a rerun can flip a
  question.
- **In-sample improvement.** The fixes were chosen by reading Run B's misses on these 20 questions.
  The held-out set shows they don't over-apply; it doesn't show they generalize.
- **Close to the examples.** Eight of the 20 questions are a worked example on another date, so the
  documented runs are partly measuring whether the model copies an example correctly.
- **Synthetic data.** The data comes from the generator in this repo, and the questions and docs have
  the same author. Real HR data and real requests are messier.
- **Moving data.** The source advances every business day. Closed periods don't change, and q02 and
  q04 had the same answers on both run dates, but a later rerun can see different values for the
  "today" questions (q02, q04, h02, h03).
- **Extra context from the CLI.** Claude Code adds a short note to every call (the machine's date, the
  temp directory, the platform and the account e-mail) that can't be turned off on the subscription.
  Its date is the machine's (2026-10-15 for these runs), past the end of the data: the same trap as
  `current_date()`.

## Reproduce

From the repo root, with `.env` set up and Claude Code logged in to a claude.ai account:

```bash
make eval CANARY=1                     # check the model can't read this machine's files
make eval EVAL=bare                    # Run A
make eval EVAL=documented              # Run B: the original docs
make eval EVAL=improved                # Run C: the current docs
make eval EVAL=improved QUESTIONS=eval/questions_holdout.yaml
make eval EVAL=documented ONLY=q04,q16 # some questions only
make docs                              # rebuild schema_context.md after editing the dbt docs or metric definitions
```

The variable is `EVAL`, because `RUN` is the Makefile's command prefix. Each run writes
`eval/results/<run>-<YYYYMMDD-HHMM>.jsonl` and a `.summary.md`. `make eval RESCORE=<file>.jsonl`
re-judges a results file's saved SQL against the current questions and today's data without calling
the model, and rewrites that file, so run it on a copy to keep the original (add
`QUESTIONS=eval/questions_holdout.yaml` for a held-out file). Rescoring copies of every file here on
2026-10-14 data gave the same status for every question.
