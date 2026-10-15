# Migration plan: Visier to BigQuery

How a people-analytics estate would move off Visier and onto BigQuery, dbt and LookML, with this repo
as the target. It goes with the [migration inventory](migration-inventory.csv) (every Visier-style
object, metric and dimension, and where it lives now) and the [reconciliation](reconciliation.md).

**This is a rehearsal.** Nodewell Labs is fictional and its data is synthetic. There is no Visier
tenant: the Visier side is hypothetical, and its definitions are emulated from the source data by
following Visier's public documentation. No figure here was exported from Visier. What is real is the
target (this warehouse) and the Visier facts, each linked to its Visier page (checked 2026-10-15).
Anything the public docs don't settle is a question for the client (section 8).

## 1. Scope and approach

**What moves**
- **Objects:** Employee (a subject), Employee Exit and Employment Start (events; some docs say Employee
  Start), Requisition, Applicant (an application, not a person), Managed Position and the Organization
  hierarchy ([definitions](https://www.visier.com/docs/definitions/successfactors)).
- **Metrics:** Headcount, Average Headcount, the turnover family, tenure, time to fill, time to hire,
  offer acceptance, applicant conversion. **Security:** who sees which population and properties.
- **Consumers:** guidebooks and analyses become LookML explores and dashboards (Data Studio on the
  `rpt_*` views today); scheduled exports and data-out connectors are replaced or retired.

**Out of scope**, retired unless the client names an owner for a replacement (question 14):
[Benchmarks](https://docs.visier.com/visier-people/Visier%20People%20Metrics/benchmarks/benchmarks%20visier.htm),
predictions and plans (both returned by the [Data Model API](https://docs.visier.com/developer/apis/analytic-model/data-model/data-model-api.htm)),
[internal comparisons](https://docs.visier.com/visier-people/Analytic%20Model/analytic-objects/analytic-objects.htm),
[overlays](https://docs.visier.com/visier-people/Analytic%20Model/analytic-objects/overlays/overlays.htm)
holding values no source system has, [Vee](https://www.visier.com/docs/descriptions/application/vee/)
and the [Visier MCP server](https://docs.visier.com/developer/agents/mcp/mcp-server.htm).

**Approach.** (1) Rebuild from the source systems, not from Visier: the warehouse reads the HRIS and ATS
through Fivetran ([D2](decisions.md)) and rebuilds history itself; Visier extracts are only the
reconciliation baseline and a one-off archive (section 5). (2) Use Visier's vocabulary in the inventory
and LookML labels, so HR users find what they had. (3) Decide every definition difference on purpose
(section 4). (4) Prove it in a parallel run on closed periods, then cut over and decommission (section 7).

## 2. Discovery: enumerate the tenant

Run discovery as a user whose permission covers all data: Super Admin "bypasses data security", and
Visier recommends a custom full-access permission instead ([Super Admin](https://docs.visier.com/developer/Studio/permissions/super%20admin%20role.htm)).
The Data Model API requires "access to the data that you request", and export connectors a user with
access to all data ([connector setup](https://docs.visier.com/developer/Studio/data/data-export-connectors/data-export-connectors-set-up.htm)).
Calls need an API key plus a security token (60-minute expiry) or OAuth 2.0 ([authentication](https://docs.visier.com/developer/apis/authentication/authentication.htm)).
Visier Technical Support enables the key ([API key](https://docs.visier.com/developer/Studio/solution%20settings/api-key-generate.htm)), so ask on day one.

| What to list | How (Visier's mechanism) | Maturity and limits | Feeds |
| --- | --- | --- | --- |
| Analytic objects, properties, dimensions and members, metrics, selection concepts | Data Model API: `GET /v1/data/model/analytic-objects`, `/analytic-objects/{id}/properties`, `/analytic-objects/{id}/dimensions`, `.../dimensions/{dim}/members`, `GET /v1/data/model/metrics` ([API](https://docs.visier.com/developer/apis/analytic-model/data-model/data-model-api.htm), [Data Query API overview](https://docs.visier.com/developer/apis/data-out/data-query/fundamentals/overview.htm), [list query tutorial](https://docs.visier.com/developer/Tutorials/data-model-query/list-query-perform.htm)) | No maturity label on its page. "Each data model is unique to each Visier tenant" | One inventory row per object, metric and dimension |
| Every object, including analyses | Application Definition: an Excel export of "subjects, events, overlays, metrics, and analyses", under Model > Settings ([docs](https://docs.visier.com/visier-people/Studio/solution%20settings/solution%20definition.htm)). Called Solution Definition before Spring 2023 ([release notes](https://docs.visier.com/visier-people/Release%20Notes/2023/SP23-release-notes.htm)) | Takes a few minutes; also a content-change audit | Inventory, and the list of analyses to rebuild |
| Real formulas and denominators | Each metric's Settings tab ([metrics](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-create.htm)) and the metric lineage tool ([validation](https://docs.visier.com/visier-people/Studio/data/validation/data-validation.htm)) | Read by hand for P1 metrics | Section 4 |
| Standard or tenant-specific | Blueprint content; changing it "may impact your ability to receive Blueprint updates" ([model overview](https://docs.visier.com/developer/Analytic%20Model/analytic-model-overview.htm)) | | An inventory note: edited or custom objects carry tenant logic |
| Hidden transformation logic | Mappings and rules (business, then system, then multi-subject) ([mappings](https://docs.visier.com/developer/Studio/data/mappings/mappings.htm), [rules](https://docs.visier.com/developer/Studio/data/rules/rules.htm)) | | dbt staging and intermediate models |
| Security | Five .xlsx reports: Permission Definitions, User Permission, Data Security, Content Security, Security Impact ([security reports](https://docs.visier.com/developer/Studio/permissions/security-reports.htm)) | | Section 6 |
| Source systems and loads | Data In Connectors ([catalogue](https://docs.visier.com/developer/Studio/data/data%20connectors/data-connectors.htm)), SFTP, the Data Upload API ([docs](https://docs.visier.com/developer/apis/data-in/data-upload/data-upload-api.htm)); `GET /v1/op/data-versions` ([data and job handling](https://docs.visier.com/developer/apis/data-in/data-job-handling/get-started.htm)) and release behavior ([auto, manual or custom](https://docs.visier.com/visier-people/Studio/data/jobs/release-behavior.htm)) | Uploads up to 500 MB; SFTP above that | One Fivetran connection per source system |
| Consumers | Reporting API ([docs](https://docs.visier.com/developer/apis/data-out/reporting/reporting-api.htm)); Data Exports; data-out connectors (Power BI, Tableau, Jupyter, SageMaker, Python), which use the Data Query API ([docs](https://docs.visier.com/developer/Studio/data/data%20connectors/data-connectors-out.htm)); Vee and MCP | Reporting API is Alpha | The cut-over list |
| Calendar | Business calendar file; Gregorian is the default ([docs](https://docs.visier.com/visier-people/Studio/data/business-calendar-upload.htm)) | | `dim_date` fiscal columns |

Discovery is done when every object, metric, property and dimension is in the inventory with an owner, a
priority (P1 to P3) and a target, each subject's source system is named, every permission is in the
security mapping (section 6) and every consumer is on the cut-over list. Each inventory row has a status:
*migrated* (built, and it follows Visier's documented definition or no open client question would change
the number), *reconciling* (built, but a known definition difference or an open question can still change
the number or who sees it; it becomes migrated once the parallel run passes or the decision is logged),
*gap* (Visier has it for this data and the warehouse doesn't yet) or *retired* (deliberately not rebuilt,
with the reason in its notes).

## 3. Mapping Visier's model to the warehouse

Visier puts its own model in warehouse terms: "the analytic object is the table, its attributes are the
columns" ([analytic objects](https://docs.visier.com/visier-people/Analytic%20Model/analytic-objects/analytic-objects.htm)),
and its glossary maps a subject to an SCD type 2 dimension, an event to a transaction fact, a concept to
derived attributes and a metric to a measure ([key terms](https://docs.visier.com/developer/Platform/key-terms.htm)).

| Visier concept | Warehouse design | Repo file |
| --- | --- | --- |
| Subject **Employee**; a member is one employee | `dim_employee`: one row per job version, `valid_from`/`valid_to` from the HRIS effective dates (valid time). `employee_id` is one employment record, `person_id` links rehires | [dim_employee.sql](../dbt/models/marts/dim_employee.sql), [dim_employee.view.lkml](../lookml/views/dim_employee.view.lkml) |
| Validity intervals, "valid until it's not"; valid time and record date ([key terms](https://docs.visier.com/developer/Platform/key-terms.htm), [data loads](https://docs.visier.com/developer/Studio/data/data-loads.htm)) | Valid time in `dim_employee`. Record (system) time in `snap_employees`, an SCD2 of the employee record built without MERGE ([D6](decisions.md)), plus `_fivetran_synced` | [snap_employees.sql](../dbt/models/history/snap_employees.sql) |
| Conception and termination events bound a subject's validity ([object settings](https://docs.visier.com/visier-people/Analytic%20Model/analytic-objects/analytic-object-settings.htm)) | `hire_date` and `termination_date` bound each record; the daily headcount only counts days between them | [fct_headcount_daily.sql](../dbt/models/marts/fct_headcount_daily.sql) |
| Event **Employee_Exit** (happens at an instant, never changes) ([events](https://docs.visier.com/visier-people/Analytic%20Model/analytic-objects/events.htm), [object settings](https://docs.visier.com/visier-people/Analytic%20Model/analytic-objects/analytic-object-settings.htm)) | `fct_terminations`: one row per FTE exit, joined to the version in effect on the exit date | [fct_terminations.sql](../dbt/models/marts/fct_terminations.sql), [view](../lookml/views/fct_terminations.view.lkml) |
| Event **Employment Start**; Hire Date and Time to Hire are its properties ([objects](https://docs.visier.com/developer/Studio/getting%20started/objects-visier-cloud.htm)) | `fct_hires`, with `days_to_hire` and `days_to_fill` on the start | [fct_hires.sql](../dbt/models/marts/fct_hires.sql), [view](../lookml/views/fct_hires.view.lkml) |
| **Internal_Placement**; a promotion is a placement filtered by `isPromotion` ([metric examples](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-examples.htm)) | `dim_employee.change_reason` (`promotion`, `transfer`, ...). No placement fact yet | [dim_employee.sql](../dbt/models/marts/dim_employee.sql) |
| Subject **Applicant** and the Applicant Process concept ([process concepts](https://docs.visier.com/developer/Analytic%20Model/concepts/process/process-configure.htm)) | `fct_application_funnel`: one row per application, `reached_*` flags and stage timestamps | [fct_application_funnel.sql](../dbt/models/marts/fct_application_funnel.sql) |
| Subject **Requisition** | `fct_requisitions`, current status only (the ATS keeps no status history) | [fct_requisitions.sql](../dbt/models/marts/fct_requisitions.sql) |
| **Managed Position**, "the set of positions that the organization is managing" ([definitions](https://www.visier.com/docs/definitions/successfactors)) | Staged only, no mart yet | [stg_hris__positions.sql](../dbt/models/staging/stg_hris__positions.sql) |
| **Organization_Hierarchy**, parent-child, shared by Employee and Managed Position ([data requirements](https://docs.visier.com/swp/Organization%20Design/data-requirements.htm)) | `dim_org`: department, parent and `division_name` roll-up (two levels). Facts carry the department on the date, which is Visier's "as-was" view ([data loads](https://docs.visier.com/developer/Studio/data/data-loads.htm)) | [dim_org.sql](../dbt/models/marts/dim_org.sql), [view](../lookml/views/dim_org.view.lkml) |
| `Employee.Direct_Manager` and the supervisory hierarchy ([dimensions](https://docs.visier.com/developer/Analytic%20Model/dimensions/dimensions.htm)) | `manager_id` on each version; no flattened manager hierarchy yet | [dim_employee.sql](../dbt/models/marts/dim_employee.sql) |
| Properties; calculated properties are formulas evaluated at a date ([properties](https://docs.visier.com/visier-people/Analytic%20Model/properties/properties.htm)) | Columns; date-dependent ones are LookML expressions, e.g. `tenure_years` at the snapshot date | [fct_headcount_daily.view.lkml](../lookml/views/fct_headcount_daily.view.lkml) |
| Dimensions, including range dimensions such as Tenure Range | LookML dimensions; `tenure_band` uses bands chosen here, not the tenant's | same, and [fct_terminations.view.lkml](../lookml/views/fct_terminations.view.lkml) |
| Selection concept, e.g. `isActiveEmployee` ([selection concepts](https://docs.visier.com/visier-people/Analytic%20Model/concepts/selection/selection-configure.htm)) | Boolean columns (`is_fte`, `is_current`, `reached_hired`) and filtered LookML measures | [lookml/views/](../lookml/views/) |
| Calculation concept, e.g. the Employee Exit Model ([concepts](https://docs.visier.com/developer/Analytic%20Model/concepts/concepts.htm)) | `termination_type` and the reason seed | [termination_reasons.csv](../dbt/seeds/termination_reasons.csv) |
| Metric (written in VFL) | LookML measure plus an entry in the metric definitions | [lookml/views/](../lookml/views/), [metric-definitions.md](metric-definitions.md) |
| Time handling: `validUntil instant` for subjects (the state at the period's last instant), `occurredIn interval` for events ([metric time](https://docs.visier.com/visier-people/Analytic%20Model/metrics/metrics-time.htm)) | Headcount on the last day of the period from `fct_headcount_daily`; event counts filter on the event date | [fct_headcount_daily.sql](../dbt/models/marts/fct_headcount_daily.sql) |
| `forInstantsInInterval` with `averageOverTime` ([metric examples](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-examples.htm)) | `avg_headcount`, a daily mean: a different method (4.2) | [fct_headcount_daily.view.lkml](../lookml/views/fct_headcount_daily.view.lkml) |
| Business calendar | `dim_date.fiscal_year`, `fiscal_quarter` (April to March) | [dim_date.sql](../dbt/models/marts/dim_date.sql) |
| Data version, "an instance of uploaded data" ([data versions](https://docs.visier.com/developer/Studio/data/jobs/data-versions.htm)) | One nightly build; its as-of date is `int_reporting_date` | [int_reporting_date.sql](../dbt/models/intermediate/int_reporting_date.sql), [dbt.yml](../.github/workflows/dbt.yml) |
| Project: draft, then publish ([projects](https://docs.visier.com/developer/Studio/projects/projects.htm)) | A branch, a pull request and the CI build | |
| Mappings and rules | Staging and intermediate models; bad data is fixed in the source ([data-quality.md](data-quality.md)) | [dbt/models/staging/](../dbt/models/staging/) |
| Guidebooks and Explore ([guidebooks](https://docs.visier.com/visier-people/Analytics/guidebooks/guidebooks.htm)) | LookML explores and dashboard; Data Studio on the reporting views | [model](../lookml/people_analytics.model.lkml), [hr_overview](../lookml/dashboards/hr_overview.dashboard.lookml), [dashboards.md](dashboards.md) |
| System Start / System Termination events, generated "to fill in gaps" ([system events](https://docs.visier.com/developer/Analytic%20Model/analytic-objects/system-events.htm)) | None. A reconciliation variance class | [reconciliation.md](reconciliation.md) |

## 4. Definition decisions

Six places where the warehouse's [metric definitions](metric-definitions.md) differ from Visier's
documented defaults. Tenants can edit Blueprint metrics, so each is checked against the tenant's metric
Settings first. The effect of each on closed periods is in [reconciliation.md](reconciliation.md); a
decision, once made, goes into [decisions.md](decisions.md) and the metric definitions.

**4.1 Headcount: who is counted.** *Warehouse:* full-time employees employed on the day, 595 on
2026-09-30 (616 with contractors and interns). "FTE" is an employment type here: it counts people.
*Visier:* Headcount (`employeeCount`) counts active employee IDs (`isActiveEmployee`) at the period's
last instant ([metric examples](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-examples.htm));
by default it "includes all current employees" and can be edited, e.g. to exclude contingent workers
([use case](https://docs.visier.com/visier-people/Platform/use-cases/build.htm)). The documented example
of current employees includes "On Notice" ([selection concepts](https://docs.visier.com/visier-people/Analytic%20Model/concepts/selection/selection-configure.htm)).
FTE is a separate factor in Visier ([release notes](https://docs.visier.com/visier-people/Release%20Notes/2019/SP19-release-notes.htm)).
*Options:* keep the full-time count as Headcount; adopt the tenant's population; or both, named apart.
*Recommendation:* both. Add a population flag that reproduces the tenant's Headcount population
(`isActiveEmployee` and any filter added to the Headcount formula), label the full-time count "Headcount
(full-time employees)", and keep "FTE" for a summed FTE factor if the client has one. The timing already
matches: both read the last day of the period. The same choice decides whether contract and internship
ends count in Turnover Rate, which is the largest difference in [reconciliation.md](reconciliation.md).

**4.2 Average headcount: daily mean or month-end trapezoid.** *Warehouse:* the mean of the daily
headcount over every day of the period (Q3 2026: 584.4). *Visier:* samples monthly instants and averages
them; February 2023 is (962 + 965) / 2, the January and February month-ends ([metric examples](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-examples.htm)).
For several months the weights are probably (h0/2 + h1 + ... + hn/2) / n, which is inferred, not
documented. Turnover Rate is "Turnover Count divided by Average Headcount" ([thresholds page](https://docs.visier.com/developer/Studio/solution%20settings/aggregate-thresholds-set.htm)),
so every rate inherits this choice. *Options:* keep the daily mean; switch to month-ends; or both.
*Recommendation:* keep the daily mean as the default (hiring or exits in waves within a month don't
distort it, and the warehouse restates all history with it, so trends stay consistent) and add a
month-end "Visier method" measure for the parallel run and for comparisons with old Visier reports.

**4.3 Voluntary is not Resignation.** *Warehouse:* `voluntary` means the person chose to leave;
retirement is its own type. Q3 2026: 20 voluntary, 3 involuntary, 1 retirement. *Visier:* Resignation,
Retirement and Other Voluntary counts "sum up to the Voluntary Turnover Count" ([concepts](https://docs.visier.com/developer/Analytic%20Model/concepts/concepts.htm)),
and "Resignations do not include retirements" ([resignation report](https://www.visier.com/research/resignation-report/)).
So our voluntary is Visier's Resignation, and Visier's Voluntary Turnover for Q3 2026 would be 21.
*Options:* rename the measures; or map each reason code into Visier's exit hierarchy. *Recommendation:*
map. Add an exit-category column to `termination_reasons` (proposed: `VOL_*` → Resignation,
`RET_RETIREMENT` → Retirement, `INV_RESTRUCTURING` → Reduction in Force, `INV_PERFORMANCE` and
`INV_MISCONDUCT` → Termination; the client confirms, question 5) and label the LookML measures with
Visier's benchmark names: Resignation Rate, Voluntary Turnover Rate, Involuntary Turnover Rate,
Retirement Rate, Employee Exit Rate ([benchmarks](https://docs.visier.com/visier-people/Visier%20People%20Metrics/benchmarks/benchmarks%20visier.htm)).
We found no Blueprint definition of regretted turnover, so `regretted_flag` stays a tenant extension (question 6).

**4.4 Time to fill: where the clock starts.** *Warehouse:* requisition opened → offer accepted,
mean and median reported. *Visier:* a Visier blog post counts "from the date a job requisition is
approved to the date an offer is accepted" ([blog](https://visier.com/blog/hr-glossary-time-to-fill));
another says "after advertising a position" ([glossary](https://www.visier.com/blog/the-hr-terms-glossary/));
the product works from requisition status changes ([Talent Acquisition](https://www.visier.com/docs/descriptions/application/vp-talent-acquisition/)).
The product formula, and mean or median, aren't public. *Options:* opened; approved; a parameter.
*Recommendation:* ask (question 8). In this ATS a requisition is created once approved and only
`opened_date` exists, so opened is the only start available. If the client's ATS records approval
separately, bring its status history in (an additive change under the [data contract](data-contract-hris.md))
and make the start status a parameter.

**4.5 Tenure: which start date.** *Warehouse:* from the current employment record's `hire_date` to the
snapshot date, in days / 365.25; a rehire restarts (2.76 years on 2026-09-30). *Visier:* the calculated
property `monthsBetween(Start_Date, effectiveDate, gregorian)`, and the Tenure metric averages Tenure/12
over active employees ([metric examples](https://docs.visier.com/developer/Analytic%20Model/metrics/metrics-examples.htm)),
a worked example; Visier's objects page instead describes the Tenure property as "time from Latest Time to
Hire Date" ([objects](https://docs.visier.com/developer/Studio/getting%20started/objects-visier-cloud.htm)).
Which date the tenant uses (`Start_Date` or Hire Date), and for rehires, depends on its mapping.
*Options:* latest hire; original hire (first `hire_date` per `person_id`); both. *Recommendation:* both,
defaulting to whatever date the tenant's Tenure uses, compared in months. Copy the tenant's Tenure Range
band edges (not public) into a seed and drive `tenure_band` from it.

**4.6 Exit period: the next-period rule.** *Warehouse:* `termination_date` is the last day employed;
the exit counts in the period containing it, and the leaver is still in that day's headcount.
*Visier:* "Metrics that track movement count ending events at the end of the time period and include the
event in the next period"; someone who worked April 30 "is counted in May exits, not April"
([events](https://docs.visier.com/visier-people/Analytic%20Model/analytic-objects/events-create.htm)).
The same page says this keeps the walk balanced: final headcount = initial + movement in + starts -
(movement out + exits). Visier's system-events page says the same for turnover: "Terminations on the last
day of the month may count toward the next month's turnover", and the system "treats the last working day
as the termination date" ([system events](https://docs.visier.com/developer/Analytic%20Model/analytic-objects/system-events.htm)).
The "may", and the page listing these among scenarios that trigger system termination events, leave the
tenant's setup to confirm (question 3). The warehouse's month-to-month walk already balances this way:
[assert_headcount_reconciles_month_to_month](../dbt/tests/assert_headcount_reconciles_month_to_month.sql)
counts an exit from the day after the last day. The published exit counts and rates don't: they go by
`termination_date`, so a September 30 leaver is in the September 30 headcount and also in Q3 exits.
*Options:* keep; bucket exits by `termination_date + 1`; carry both. *Recommendation:* carry both
(proposed `exit_effective_date`, which the walk test then uses in place of its inline
`termination_date + 1`). Extend the existing walk test to each division, adding transfers in and out
(taken from `dim_employee` version changes until a placement fact exists). For turnover rates, follow the
tenant once a Visier export shows which rule it applies (question 3).

## 5. Extraction and loading

| Option | What it gives | Limits | Fit here |
| --- | --- | --- | --- |
| **Data Version Export API** (Alpha) ([docs](https://docs.visier.com/developer/apis/data-out/data-version-export/fundamentals/overview.htm)) | Visier's processed tables for one data version (full) or the delta between two | gzip CSV per table, split above 2GB; adds `_DiffAction_` and `EventIndexOnDate`; dates are millisecond timestamps | One-off history archive before decommission |
| **Detailed / Aggregated Data Exports** ([docs](https://docs.visier.com/developer/Analytics/data%20export/data-export.htm)) | Attribute values for subjects and events, or metric values | Up to 7.5 million cells (Detailed) or 300,000 (Aggregated); a default limit of 1,000 exports per project and per user ([create](https://docs.visier.com/developer/Analytics/data%20export/data-export-create.htm)); read with the "Consume Data Exports" capability, under the user's security ([consume](https://docs.visier.com/developer/Analytics/data%20export/data-export-consume-visier-api.htm)) | Reconciliation baselines (Aggregated); archive fallback (Detailed) |
| **Data Export Connectors**, including **Google BigQuery** (feature Limited Availability, [API](https://docs.visier.com/developer/apis/data-out/data-export-connectors/data-export-connectors-api.htm) Alpha) ([docs](https://docs.visier.com/developer/Studio/data/data-export-connectors/data-export-connectors.htm)) | Pushes versioned, Active Data Exports to a BigQuery table, run now or on a schedule once published to production | Each export goes to exactly one table and table names are permanent ([setup](https://docs.visier.com/developer/Studio/data/data-export-connectors/data-export-connectors-set-up.htm)); runs as the scheduling user, who needs access to all data; needs a service account with a JSON key, the Data Lineage API, BigQuery Job User and Data Lineage Events Producer on the project and BigQuery Data Editor on the dataset ([requirements](https://docs.visier.com/visier-people/Studio/data/data-export-connectors/google/google-bigquery-export-reqs.htm)). Overwrite or append isn't documented | Daily side-by-side numbers in the parallel run, if already licensed |
| **Data Query API** ([overview](https://docs.visier.com/developer/apis/data-out/data-query/fundamentals/overview.htm)) | Aggregate queries, list queries (records at an instant), SQL-like queries (needs the Enterprise API User profile, [docs](https://docs.visier.com/developer/apis/sql-like/sql-like-querying.htm)) | 100 calls a minute or 6,000 an hour; aggregate cell sets up to 500,000 cells; aggregate queries are "not intended for bulk extraction", and Visier advises against running them through service accounts ([best practices](https://docs.visier.com/developer/apis/data-out/data-query/fundamentals/best-practices.htm)) | Spot checks, e.g. the active employee IDs at a month-end, diffed against the warehouse |
| **Source Files Download API** (Alpha) ([docs](https://docs.visier.com/developer/apis/data-out/source-files-download/source-files-download-api.htm)) | The raw files uploaded to Visier, by timestamp | | Recovering source history files for backfill and lineage |
| **Back to the source systems through Fivetran** (this repo) | HRIS and ATS tables replicated with change data capture, landed, batch-loaded to BigQuery; dbt rebuilds history from effective dates ([D2](decisions.md), [D6](decisions.md)) | The source must hold the history, or the warehouse must have been recording it (`snap_employees`) | The go-forward pipeline |

**Recommendation.** The go-forward pipeline is source systems → Fivetran → BigQuery → dbt, which is how
this repo is designed (until its Fivetran connection is live, a bootstrap load copies the same tables).
Visier is being switched off, and what it holds is a processed copy that includes system events, exits
inferred from missing records ([data loads](https://docs.visier.com/developer/Studio/data/data-loads.htm))
and rules applied inside Visier, so nothing ongoing should depend on it. No Fivetran source connector
for Visier turned up in research; that doesn't matter for this design. Visier extracts are used for:
- **Baselines:** Aggregated Data Exports for the P1 metrics, pulled by a full-access user and recorded
  with the data version number they came from. Employee-level differences are chased with list queries.
- **An archive:** before decommission, a full Data Version Export of the last data version if the Alpha
  API is enabled for the tenant, otherwise Detailed Data Exports per object, landed untransformed in a
  restricted dataset (proposed `raw_visier`). It is used only where the sources have lost history, e.g.
  attributes overwritten in place before the warehouse started recording them.
- **Data Export Connectors** only if already licensed and the parallel run needs daily figures, and only
  after Visier confirms how the BigQuery table is written.

## 6. Security mapping

A Visier permission bundles data security, capabilities and content packages ([security model](https://docs.visier.com/visier-people/Studio/permissions/security%20model.htm)).
The repo has one `access_filter` on `dim_org.division_name` (user attribute `division`) on each of
its three explores, and the `can_see_salary` grant on `salary_local` and `currency` ([model](../lookml/people_analytics.model.lkml)).

| Visier | BigQuery | LookML | Gap |
| --- | --- | --- | --- |
| **Population access**: static member filters (ORed within a dimension, ANDed across), dynamic filters that follow the user's place in the hierarchy ([security filters](https://docs.visier.com/developer/Studio/permissions/data%20security/security%20filters.htm)), or a file mapping users to EmployeeID, RequisitionID or ApplicantID ([security file](https://docs.visier.com/developer/Studio/permissions/security-from-file.htm)) | Row access policies, `FILTER USING` a lookup on `SESSION_USER()` ([row-level security](https://cloud.google.com/bigquery/docs/managing-row-level-security)), from a user-to-division table rebuilt nightly from `dim_employee` | `access_filter`, on every explore: one left out shows all data ([access_filter](https://cloud.google.com/looker/docs/reference/param-explore-access-filter)) | Requisition and Applicant need scoping too (the recruiting explore has it through `dim_org`) |
| **History**: with access to a member "you can see the historical data of that member" ([security model](https://docs.visier.com/visier-people/Studio/permissions/security%20model.htm)); Advanced Historical Analysis is implicitly assigned ([capabilities](https://docs.visier.com/developer/Studio/permissions/capabilities.htm)) | Filter on the current or the as-of division | The repo filters on the division *on the date*, so a division head doesn't see the earlier history of someone who moved in | Decide per tenant (question 11) |
| **Data access sets**: Detailed, Aggregate or None per property ([data access sets](https://docs.visier.com/developer/Studio/permissions/data%20security/data%20access%20sets.htm)) | None ≈ a policy tag without Fine-Grained Reader ([column-level security](https://cloud.google.com/bigquery/docs/column-level-security-intro)); [masking](https://cloud.google.com/bigquery/docs/column-data-masking-intro); aggregates only through an [authorized view](https://cloud.google.com/bigquery/docs/authorized-views) | `access_grant` and `required_access_grants`, as for salary ([access_grant](https://cloud.google.com/looker/docs/reference/param-model-access-grant)) | Aggregate has no one-to-one equivalent; hiding a field can change measure results ([required_access_grants](https://cloud.google.com/looker/docs/reference/param-field-required-access-grants)) |
| **Aggregate thresholds**: "N/A" below a minimum population, none by default, also applied to the average-headcount population, skipped for Detailed users ([thresholds](https://docs.visier.com/developer/Studio/solution%20settings/aggregate-thresholds-set.htm)) | `CASE` / `HAVING` in reporting views | `CASE WHEN count < k THEN NULL` in measures | Record the tenant's setting; an expected parallel-run variance |
| **Full access**: Super Admin bypasses security; a custom full-access permission is preferred | A row policy with `TRUE` for full-access principals: anyone outside every grantee list sees no rows ([row-level security](https://cloud.google.com/bigquery/docs/row-level-security-intro)) | Admins still need a user attribute value (`%, NULL`) | The reconciliation user needs this |
| **Capabilities and profiles** ([profiles](https://docs.visier.com/visier-people/Studio/users/profiles/profiles.htm)) | IAM roles | Looker roles and permission sets | |
| **Content packages** hide content but not from Detailed View ([content packages](https://docs.visier.com/visier-people/Studio/permissions/content%20packages/content%20package.htm)) | n/a | Model sets, folders, hidden fields | UX, not security, on both sides |
| **Exports** carry what "the scheduling user is allowed to see", and "the target system applies its own security model" ([connectors](https://docs.visier.com/developer/Studio/data/data-export-connectors/data-export-connectors.htm)) | Dataset IAM, policy tags, row policies on the landing dataset | n/a | Exported data lands unscoped unless we scope it |

**Union versus AND.** Visier permissions add up: "your security is the sum of the access from each", and
the least restrictive wins a conflict ([multiple permissions](https://docs.visier.com/developer/Studio/permissions/view-details-multiple-permissions.htm),
[security model](https://docs.visier.com/visier-people/Studio/permissions/security%20model.htm)).
BigQuery row policies also union: a user gets the rows of every policy they are granted
([row-level security](https://cloud.google.com/bigquery/docs/managing-row-level-security)). Looker does
not: several `access_filter`s on one explore are all applied, so they AND. Someone holding a Sales and a
Marketing permission in Visier would see only the overlap if each became its own `access_filter`. So keep
**one** `access_filter` per explore, and give such a user a String Filter (advanced) attribute listing
every division they may see, the mechanism Looker documents for admins ([access_filter](https://cloud.google.com/looker/docs/reference/param-explore-access-filter)).
Row policies see only the querying identity: they protect each Looker user only if Looker queries
BigQuery as that user. With a shared service account, `access_filter` is the control inside Looker and
row policies protect every other route (Data Studio, notebooks, scripts, text-to-SQL tools).

**PII.** Names and email (`dim_employee`) get a PII policy tag; salary and currency a compensation tag.
Visier itself warns that users "can still infer sensitive values, such as compensation, from aggregate
values" and recommends None ([data access sets](https://docs.visier.com/developer/Studio/permissions/data%20security/data%20access%20sets.htm)),
which backs gating salary entirely. The reporting views (`rpt_*`) carry no names, email or salary. Any
Visier extract lands in a restricted dataset with no broad readers and is deleted when the agreed
retention ends. Tools that query for someone else, text-to-SQL tools included, run as an identity without
Fine-Grained Reader, so tagged columns are refused.

## 7. Phased plan

| Phase | Work | Owner (role) | Done when |
| --- | --- | --- | --- |
| 1. Discovery (2 weeks) | Section 2; P1 list; open questions sent | Data engineer; Visier admin runs exports; People Analytics lead sets priorities | Inventory complete with owners and priorities; every open question answered or assigned |
| 2. Build (3 to 5 weeks) | Close inventory gaps: population flag, exit categories, Visier-method average, exit-period date, tenure from original hire, walk test per division, positions mart if P1; row policies and policy tags; LookML labels in Visier names; dashboards for the guidebooks in use | Data engineer; Looker developer; cloud admin for IAM and policy tags | `dbt build` green including the walk test per division; each inventory row migrated or retired with sign-off; a test user per Visier permission sees the same population size in both systems on a closed month-end |
| 3. Parallel run (two month-end closes, one of them a quarter-end) | Both systems live; reconcile closed periods only; Visier figures tied to a data version; each variance classed as definition, data, timing or security | Data engineer runs it; HRIS lead fixes data in the source; People Analytics lead signs off | Two closes in a row within tolerance (below); sign-off recorded in [reconciliation.md](reconciliation.md) |
| 4. Cut-over (1 week) | Freeze Visier content changes; move each consumer on the cut-over list; tell users where things went; Visier stays readable | People Analytics lead (business); data engineer (technical) | Every consumer moved or retired; first close reported from the warehouse alone |
| 5. Decommission (before the licence ends) | Archive the last data version, Application Definition, security reports and metric Settings; regenerate the API key (it doesn't expire, and regenerating invalidates the old one, [API key](https://docs.visier.com/developer/Studio/solution%20settings/api-key-generate.htm)) and remove API users; delete the export connector's service-account key; stop Visier's inbound feeds | Visier admin; cloud admin; data engineer | Archive restorable and queryable; no Visier credentials left; contract closed |

**Tolerance (proposed, agreed in discovery).** Counts (headcount, exits, starts): exact, or each
difference traced to employee IDs. Rates: within ±0.5 percentage points. Averages (average headcount,
tenure, time to fill, time to hire): within 2%. A gap outside tolerance that documented definition
differences fully account for is *explained*; anything left is a data problem and fails. Compare closed
periods only (Q3 2026, calendar 2025, month-ends up to 2026-09-30): Visier's data versions and the nightly
build land at different times, and here the source moves a business day per run, so "today" can differ.

**Accepted when** every P1 metric passes two closes, every definition decision is logged with its owner,
every permission and consumer has a tested replacement or a named approver for retiring it, and the
archive reads without Visier.

**The first 30 days, for a contractor joining mid-stream**
- **Days 1 to 2:** request access (a full-access Visier permission, the API key through Visier Technical
  Support, the BigQuery project, Fivetran, Looker). Read the [data contract](data-contract-hris.md),
  [metric definitions](metric-definitions.md), [decisions](decisions.md) and runbooks.
- **Days 3 to 5:** Application Definition, Data Model API dump, security reports, data versions and
  source connectors. First inventory draft. Agree the P1 list with the People Analytics lead and send
  the open questions.
- **Week 2:** read the P1 metrics' Settings. Pull Aggregated Data Exports for Q3 2026, 2025 and the 2026
  month-ends; first reconciliation; class every variance.
- **Week 3:** one short session per definition decision with the People Analytics lead; build the agreed
  measures and seeds on a branch; prototype security for two permissions end to end; extend the walk test
  to divisions.
- **Week 4:** reconcile the September close with an employee-level diff; write the parallel-run runbook
  and the cut-over checklist; present a dated plan, the risk log and the decisions still open.

## 8. Risks and open questions

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Definitions differ from Visier's (section 4) | Trusted numbers change at cut-over | Decide each one in writing; keep Visier-method measures for a year |
| Visier-only data: system events, exits inferred from temporal loads, rolling restatements ([data loads](https://docs.visier.com/developer/Studio/data/data-loads.htm)) | Variances with no source record | A named variance class; archive the last data version |
| Alpha and Limited Availability features change or aren't licensed | Archive or daily comparison not possible | Ask early (question 12); Detailed Data Exports as fallback |
| Security semantics: union vs AND, history access, thresholds, Aggregate access | Over- or under-exposure | Section 6; a test user per permission |
| Visier extracts land everything the full-access user can see | PII in a broad dataset | Restricted landing dataset, retention date |
| Source systems lack history Visier holds | Gaps before the warehouse started recording | Archive first; `snap_employees` from day one |
| Hidden consumers (connectors, scheduled exports, Vee, MCP) | Something breaks after decommission | Consumer list from discovery; read-only Visier period |
| Visier's doc sets differ in places, e.g. two capability lists for Data Export Connectors ([page 1](https://docs.visier.com/developer/Studio/data/data-export-connectors/data-export-connectors.htm), [page 2](https://docs.visier.com/developer/apis/data-out/data-export-connectors/data-export-connectors-api.htm)) | Wrong access requested | Confirm with the Visier admin or support |

**Open questions for the client**

| # | Topic | Question |
| --- | --- | --- |
| 1 | Population | How is `isActiveEmployee` defined: are On Notice, leave or contingent workers included? Have any Blueprint metrics been edited (for example Headcount without contingent workers)? |
| 2 | Loads | Is Employee's time granularity Day or Month? Are loads temporal or transactional, and is release automatic or manual? |
| 3 | Exits | Is a termination date the last day worked? Do exit metrics follow the next-period rule? |
| 4 | Rates | For Resignation Rate, Voluntary Turnover Rate and Employee Exit Rate: which denominator, and plain, annualised or trailing 12 months? For Average Headcount over a quarter or a year, which month-end instants and weights are used? Please share the metric Settings and lineage. |
| 5 | Exit reasons | Which reasons map to Resignation, Other Voluntary, Termination, Reduction in Force and Other Involuntary? |
| 6 | Regretted | Is "regretted" an exit flag or derived from performance data? Is it reported as a rate or a share of exits? |
| 7 | Tenure | Which date feeds Tenure (`Start_Date` or Hire Date), and which date is it for rehires? What are the Tenure Range band edges? |
| 8 | Recruiting | Does Time to Fill start at Approved or Open, and is it a mean or a median? Is Time to Hire a mean or a median, and does it start at the application or at sourcing? Is offer acceptance measured against extended or answered offers? |
| 9 | Calendar | Is a business calendar the default? Do key metrics still hard-code `gregorian`? |
| 10 | Hierarchy | Are org hierarchies as-was, as-is or both? Is security applied on the as-is hierarchy? |
| 11 | Security | Which permissions, dynamic filters and security files exist? Are aggregate thresholds or Advanced Historical Analysis in use? Is there more than one analytic tenant ([tenants](https://docs.visier.com/developer/Studio/tenant-mgmt/tenant-hierarchy.htm))? |
| 12 | Licensing | Which features are licensed: Data Export Connectors (Limited Availability), Data Version Export (Alpha), API keys? |
| 13 | Consumers | Who uses Visier today (Power BI or Tableau connectors, Data Exports, Vee, MCP, Copilot), and what must be replaced at cut-over? |
| 14 | Retirement | Which Visier capabilities with no warehouse equivalent can be retired: Benchmarks, predictions, planning? |
